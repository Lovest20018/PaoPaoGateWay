//! Fixed loopback bridge for the Debian administrator UI, after native bearer/ACL checks.
use crate::{http, respond};
use std::io;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

pub(crate) const MAX_BODY: usize = 1_100_000;
const MAX_RESPONSE: usize = 2 * 1024 * 1024;

fn target(method: &str, path: &str) -> Option<&'static str> {
    match (method, path) {
        ("GET", "/sniffbox/subscription/status") => Some("/api/status"),
        ("GET", "/sniffbox/subscription/config") => Some("/api/config"),
        ("POST", "/sniffbox/subscription/apply") => Some("/api/subscription"),
        ("GET", "/sniffbox/subscription/maintenance") => Some("/api/maintenance"),
        ("POST", "/sniffbox/subscription/core/check") => Some("/api/core/check"),
        ("POST", "/sniffbox/subscription/core/update") => Some("/api/core/update"),
        ("POST", "/sniffbox/subscription/core/rollback") => Some("/api/core/rollback"),
        ("POST", "/sniffbox/subscription/policy/reset") => Some("/api/policy/reset"),
        _ => None,
    }
}

async fn request(method: &str, target: &str, token: &str, body: &[u8]) -> io::Result<Vec<u8>> {
    let mut upstream = TcpStream::connect("127.0.0.1:8789").await?;
    let head = format!(
        "{method} {target} HTTP/1.1\r\nHost: 127.0.0.1:8789\r\nOrigin: http://127.0.0.1:8789\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    upstream.write_all(head.as_bytes()).await?;
    upstream.write_all(body).await?;
    let mut raw = Vec::new();
    upstream.take((MAX_RESPONSE + 1) as u64).read_to_end(&mut raw).await?;
    if raw.len() > MAX_RESPONSE {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "admin response too large"));
    }
    Ok(raw)
}

pub(crate) async fn handle(
    stream: &mut TcpStream,
    req: &http::ReqHead,
    token: &str,
    ka: bool,
    body: &[u8],
) -> io::Result<()> {
    let Some(path) = target(&req.method, &req.path) else {
        return respond::send(stream, 405, "Method Not Allowed", &[], b"", ka).await;
    };
    let result = tokio::time::timeout(Duration::from_secs(12), request(&req.method, path, token, body)).await;
    if let Ok(Ok(raw)) = result {
        if let Some(end) = raw.windows(4).position(|v| v == b"\r\n\r\n").map(|v| v + 4) {
            if let Ok(head) = http::parse_response(&raw[..end]) {
                let body = &raw[end..];
                if matches!(head.framing(false), Ok(http::Framing::Length(n)) if n == body.len() as u64)
                    && serde_json::from_slice::<serde_json::Value>(body).is_ok()
                {
                    return respond::send(stream, head.status, &head.reason,
                        &[("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")],
                        body, ka).await;
                }
            }
        }
    }
    respond::send(stream, 503, "Service Unavailable",
        &[("Content-Type", "application/json"), ("Cache-Control", "no-store")],
        br#"{"error":"Subscription service is unavailable"}"#, ka).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_fixed_admin_operations_are_forwarded() {
        assert_eq!(target("GET", "/sniffbox/subscription/status"), Some("/api/status"));
        assert_eq!(target("GET", "/sniffbox/subscription/config"), Some("/api/config"));
        assert_eq!(target("POST", "/sniffbox/subscription/apply"), Some("/api/subscription"));
        assert!(target("GET", "/sniffbox/subscription/apply").is_none());
        assert!(target("POST", "/sniffbox/subscription/status").is_none());
        assert!(target("GET", "/sniffbox/subscription/status?url=http://example.com").is_none());
        assert!(target("POST", "/api/login").is_none());
    }
}
