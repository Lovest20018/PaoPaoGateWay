//! Bounded TCP observations, populated by the actual forwarding lifecycle.
//! No shell, configuration write, DNS query, or FakeIP allocation occurs here.
use crate::config::OutboundMode;
use crate::runtime::SharedState;
use parking_lot::Mutex;
use sb_stats::{ConnId, ConnRecord};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::{HashMap, VecDeque};
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

pub const CAPACITY: usize = 1024;
pub const RETENTION_SECONDS: u64 = 300;

fn now() -> f64 {
    sb_stats::now_epoch_ms() as f64 / 1000.0
}

struct Entry {
    value: Value,
    connected: bool,
}

#[derive(Default)]
struct State {
    active: HashMap<u64, Entry>,
    closed: VecDeque<Value>,
    closed_times: VecDeque<Instant>,
    sequence: u64,
    discarded: u64,
}

pub struct Observations {
    mode: OutboundMode,
    state: Mutex<State>,
}

impl Observations {
    pub fn new(mode: OutboundMode) -> Self {
        Self { mode, state: Mutex::new(State::default()) }
    }

    fn prune(state: &mut State, _sampled_at: f64) {
        while state.closed_times.front().is_some_and(|t|
            t.elapsed() > Duration::from_secs(RETENTION_SECONDS)) {
            state.closed.pop_front();
            state.closed_times.pop_front();
            state.discarded += 1;
        }
    }

    pub fn begin(&self, rec: &ConnRecord, revision: &str) {
        let mut state = self.state.lock();
        Self::prune(&mut state, now());
        if state.active.len() >= CAPACITY {
            state.discarded += 1;
            return;
        }
        let (mode, outbound, reason) = match self.mode {
            OutboundMode::Free => ("free", Some("direct"), "free_mode_no_rule"),
            OutboundMode::Socks5 => ("socks5", Some("socks5-upstream"), "fixed_socks5_upstream"),
            OutboundMode::Ovpn => ("ovpn", Some("openvpn-tunnel"), "fixed_openvpn_tunnel"),
            OutboundMode::Yaml => ("yaml", None, "mihomo_decision_unavailable"),
            OutboundMode::Suburl => ("suburl", None, "mihomo_decision_unavailable"),
        };
        let sampled_at = now();
        let value = json!({"id": rec.id.0.to_string(),
            "started_at": rec.created_epoch_ms as f64 / 1000.0, "ended_at": null,
            "last_observed_at": sampled_at, "state": "connecting", "sequence": null,
            "transport": "tcp", "inbound": rec.inbound.as_str(),
            "source_address": rec.src.0.to_string(), "source_port": rec.src.1,
            "original_destination": rec.dst.0.to_string(), "destination_port": rec.dst.1,
            "domain": rec.domain, "domain_source": rec.proto.as_str(),
            "actual_target_address": null, "upstream_peer": null,
            "resolution": {"source": "unknown", "server": null, "domain": null,
                "selected_address": null, "complete_answers": false},
            "upload": 0, "download": 0,
            "decision": {"status": if outbound.is_some() { "observed" } else { "unavailable" },
                "source": "sniffbox_outbound_dispatch", "rule_id": format!("mode:{mode}"),
                "rule_kind": "mode_dispatch", "reason": reason,
                "outbound_id": outbound, "fallback": if outbound.is_some() { Some(false) } else { None },
                "config_revision": revision, "decided_at": sampled_at,
                "connect_result": "unknown", "failure_stage": null,
                "application_result": "unknown"}});
        state.active.insert(rec.id.0, Entry { value, connected: false });
    }

    pub fn resolution(&self, id: ConnId, domain: Option<&str>, server: Option<SocketAddr>) {
        if let Some(entry) = self.state.lock().active.get_mut(&id.0) {
            entry.value["resolution"] = json!({"source": if domain.is_none() { "literal_destination" }
                else if server.is_some() { "gateway_resolver_or_cache" } else { "remote_proxy_unknown" },
                "server": server.map(|s| s.to_string()), "domain": domain,
                "selected_address": null, "complete_answers": false});
        }
    }

    pub fn connected(&self, id: ConnId, peer: Option<SocketAddr>) {
        if let Some(entry) = self.state.lock().active.get_mut(&id.0) {
            entry.connected = true;
            entry.value["state"] = json!("active");
            entry.value["last_observed_at"] = json!(now());
            entry.value["decision"]["connect_result"] = json!("connected");
            entry.value["upstream_peer"] = json!(peer.map(|p| p.to_string()));
            if matches!(self.mode, OutboundMode::Free | OutboundMode::Ovpn) {
                entry.value["actual_target_address"] = json!(peer.map(|p| p.ip().to_string()));
                entry.value["resolution"]["selected_address"] = json!(peer.map(|p| p.ip().to_string()));
            }
        }
    }

    pub fn connect_failed(&self, id: ConnId) {
        if let Some(entry) = self.state.lock().active.get_mut(&id.0) {
            entry.value["decision"]["connect_result"] = json!("failed");
            entry.value["decision"]["failure_stage"] = json!("outbound_connect");
        }
    }

    pub fn finish(&self, rec: &ConnRecord) {
        let mut state = self.state.lock();
        let Some(mut entry) = state.active.remove(&rec.id.0) else { return };
        let sampled_at = now();
        Self::prune(&mut state, sampled_at);
        state.sequence += 1;
        entry.value["sequence"] = json!(state.sequence);
        entry.value["state"] = json!(if entry.connected { "closed" } else { "failed_or_cancelled" });
        entry.value["ended_at"] = json!(sampled_at);
        entry.value["last_observed_at"] = json!(sampled_at);
        entry.value["upload"] = json!(rec.upload.load(Ordering::Relaxed));
        entry.value["download"] = json!(rec.download.load(Ordering::Relaxed));
        state.closed.push_back(entry.value);
        state.closed_times.push_back(Instant::now());
        while state.closed.len() > CAPACITY {
            state.closed.pop_front();
            state.closed_times.pop_front();
            state.discarded += 1;
        }
    }

    fn page(&self, shared: &SharedState, q: &Query) -> Value {
        let sampled_at = now();
        let mut state = self.state.lock();
        Self::prune(&mut state, sampled_at);
        let matches = |v: &&Value| {
            q.domain.as_ref().is_none_or(|d| v["domain"].as_str() == Some(d.as_str()))
                && q.client.as_ref().is_none_or(|c| v["source_address"].as_str() == Some(c.as_str()))
                && q.since.is_none_or(|t| v["last_observed_at"].as_f64().unwrap_or(0.0) >= t)
                && q.until.is_none_or(|t| v["last_observed_at"].as_f64().unwrap_or(0.0) <= t)
        };
        let oldest = state.closed.front().and_then(|v| v["sequence"].as_u64());
        let mut items: Vec<Value> = if q.active {
            state.active.values().map(|e| &e.value).filter(matches).cloned().collect()
        } else {
            state.closed.iter().filter(|v| v["sequence"].as_u64().unwrap_or(0) > q.after)
                .filter(matches).cloned().collect()
        };
        if q.active {
            items.sort_by(|a,b| a["id"].as_str().cmp(&b["id"].as_str()));
            for item in &mut items {
                if let Some(rec) = item["id"].as_str().and_then(|s| s.parse::<u64>().ok())
                    .and_then(|id| shared.conn_table.get(ConnId(id))) {
                    item["upload"] = json!(rec.upload.load(Ordering::Relaxed));
                    item["download"] = json!(rec.download.load(Ordering::Relaxed));
                }
            }
        }
        let total = items.len();
        items = items.into_iter().skip(q.offset).take(q.limit).collect();
        let next = items.last().and_then(|v| v["sequence"].as_u64()).unwrap_or(q.after);
        json!({"sampled_at": sampled_at, "boot_id": shared.integration.boot_id,
            "data": {"status": if items.is_empty() { "no_records" } else { "available" },
                "items": items, "total": total, "truncated": total > q.offset + q.limit,
                "next_sequence": if total > q.offset + q.limit { next } else { state.sequence },
                "oldest_sequence": oldest, "latest_sequence": state.sequence,
                "gap": q.after > 0 && oldest.map_or(state.sequence > q.after,
                    |first| q.after.saturating_add(1) < first),
                "discarded_records": state.discarded, "capacity": CAPACITY,
                "retention_seconds": RETENTION_SECONDS, "complete_history": false,
                "scope": "tcp_after_sniff", "view": if q.active { "active" } else { "recent" }}})
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Query {
    resource: String,
    domain: Option<String>,
    ip: Option<Ipv4Addr>,
    client: Option<String>,
    since: Option<f64>,
    until: Option<f64>,
    #[serde(default)] after: u64,
    #[serde(default)] active: bool,
    #[serde(default)] offset: usize,
    #[serde(default = "default_limit")] limit: usize,
}
fn default_limit() -> usize { 100 }

pub fn query(shared: &SharedState, raw: &str) -> Value {
    let Ok(mut q) = serde_json::from_str::<Query>(raw) else {
        return json!({"error": "INVALID_QUERY"});
    };
    if q.limit == 0 || q.limit > 200 || q.offset > 10000
        || q.since.is_some_and(|t| !t.is_finite() || t < 0.0)
        || q.until.is_some_and(|t| !t.is_finite() || t < 0.0)
        || q.client.as_ref().is_some_and(|c| c.parse::<IpAddr>().is_err()) {
        return json!({"error": "INVALID_QUERY"});
    }
    if let Some(domain) = q.domain.as_mut() {
        *domain = domain.trim_end_matches('.').to_ascii_lowercase();
        if domain.is_empty() || domain.len() > 253 || !domain.bytes().all(|b|
            b.is_ascii_alphanumeric() || b == b'.' || b == b'-' || b == b'_') {
            return json!({"error": "INVALID_DOMAIN"});
        }
    }
    if q.resource == "connections" || q.resource == "decisions" {
        return shared.observations.page(shared, &q);
    }
    if q.resource != "fakeip" || q.domain.is_some() == q.ip.is_some() {
        return json!({"error": "INVALID_QUERY"});
    }
    let Some(pool) = &shared.fakeip else { return json!({"error": "UNSUPPORTED"}) };
    let mapping = match (&q.domain, q.ip) {
        (Some(domain), None) => pool.peek_domain(domain).map(|ip| (domain.clone(), ip)),
        (None, Some(ip)) => pool.peek_ip(ip).map(|domain| (domain.to_string(), ip)),
        _ => None,
    };
    json!({"sampled_at": now(), "boot_id": shared.integration.boot_id,
        "data": {"status": if mapping.is_some() { "matched" } else { "no_records" },
            "source": "actual_fakeip_cache", "domain": mapping.as_ref().map(|m| &m.0),
            "fakeip": mapping.as_ref().map(|m| m.1.to_string()), "cidr": pool.cidr().to_string(),
            "dns_ttl_seconds": pool.ttl(), "valid_until": null,
            "validity": "until_evicted_or_core_restart", "algorithm_prediction": false,
            "historical_ownership_verified": false}})
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{Config, DnsCfg};
    use sb_stats::SniffedProto;

    fn record(id: u64) -> ConnRecord {
        ConnRecord::new(ConnId(id), ("192.0.2.1".parse().unwrap(), 1234),
            ("7.1.2.3".parse().unwrap(), 443), SniffedProto::Tls, Some("example.com".into()))
    }

    #[test]
    fn short_connection_has_real_outbound_and_bounded_history() {
        let shared = SharedState::new(&Config::default());
        for id in 1..=CAPACITY + 2 {
            let rec = record(id as u64);
            shared.observations.begin(&rec, "revision-one");
            shared.observations.connected(rec.id, Some("93.184.215.14:443".parse().unwrap()));
            rec.upload.store(9, Ordering::Relaxed);
            shared.observations.finish(&rec);
        }
        let page = query(&shared, r#"{"resource":"decisions","limit":1,"after":1}"#);
        assert_eq!(page["data"]["discarded_records"], 2);
        assert_eq!(page["data"]["gap"], true);
        let row = &page["data"]["items"][0];
        assert_eq!(row["sequence"], 3);
        assert_eq!(row["upload"], 9);
        assert_eq!(row["actual_target_address"], "93.184.215.14");
        assert_eq!(row["decision"]["outbound_id"], "direct");
        assert_eq!(row["decision"]["connect_result"], "connected");
        assert_eq!(row["decision"]["application_result"], "unknown");
        assert_eq!(row["decision"]["config_revision"], "revision-one");
        assert!(row["ended_at"].is_number());
        assert_eq!(query(&shared, r#"{"resource":"connections","client":"192.0.2.2"}"#)["data"]["status"], "no_records");
    }

    #[test]
    fn retention_eviction_and_failures_are_explicit() {
        let shared = SharedState::new(&Config::default());
        let rec = record(1);
        shared.observations.begin(&rec, "rev");
        shared.observations.connect_failed(rec.id);
        shared.observations.finish(&rec);
        let page = query(&shared, r#"{"resource":"connections"}"#);
        assert_eq!(page["data"]["items"][0]["decision"]["failure_stage"], "outbound_connect");
        shared.observations.state.lock().closed_times[0] = Instant::now() - Duration::from_secs(301);
        let page = query(&shared, r#"{"resource":"connections"}"#);
        assert_eq!(page["data"]["status"], "no_records");
        assert_eq!(page["data"]["discarded_records"], 1);
    }

    #[test]
    fn fakeip_queries_read_actual_cache_without_predictions() {
        let cfg = Config { dns: Some(DnsCfg::default()), ..Config::default() };
        let shared = SharedState::new(&cfg);
        assert_eq!(query(&shared, r#"{"resource":"fakeip","domain":"example.com"}"#)["data"]["status"], "no_records");
        let pool = shared.fakeip.as_ref().unwrap();
        let ip = pool.intern("example.com");
        let page = query(&shared, r#"{"resource":"fakeip","domain":"EXAMPLE.COM."}"#);
        assert_eq!(page["data"]["fakeip"], ip.to_string());
        assert_eq!(page["data"]["source"], "actual_fakeip_cache");
        assert_eq!(page["data"]["valid_until"], Value::Null);
        assert_eq!(query(&shared, &json!({"resource":"fakeip","ip":ip}).to_string())["data"]["domain"], "example.com");
        assert!(query(&shared, r#"{"resource":"fakeip","domain":"a","ip":"7.1.1.1"}"#).get("error").is_some());
    }

    #[test]
    fn mihomo_mode_never_claims_its_rule_or_final_peer() {
        let mut cfg = Config::default();
        cfg.outbound.mode = OutboundMode::Yaml;
        let observations = Observations::new(cfg.outbound.mode);
        let rec = record(1);
        observations.begin(&rec, "revision");
        observations.connected(rec.id, Some("127.0.0.1:1080".parse().unwrap()));
        observations.finish(&rec);
        let state = observations.state.lock();
        assert_eq!(state.closed[0]["decision"]["status"], "unavailable");
        assert_eq!(state.closed[0]["actual_target_address"], Value::Null);
    }
}
