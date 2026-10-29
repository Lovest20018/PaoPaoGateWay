// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use crate::ConfigSource;
use crate::runtime::SharedState;
use std::sync::Arc;
use tokio::signal::unix::{SignalKind, signal};

pub async fn run_reload_loop(shared: Arc<SharedState>, source: ConfigSource) {
    let mut sighup = match signal(SignalKind::hangup()) {
        Ok(s) => s,
        Err(e) => {
            tracing::warn!(?e, "failed to register SIGHUP handler");
            return;
        }
    };
    while sighup.recv().await.is_some() {
        tracing::info!(source = %source.describe(), "SIGHUP — reloading config");
        match source.load() {
            Ok(new) => {
                if std::env::var("PPGW_DEBIAN").as_deref() == Ok("1") && !shared.integration.permits_hot_reload(&new) {
                    tracing::warn!("Changed listener/mode/statistics enablement requires restart; keeping loaded config");
                    continue;
                }
                shared.reload(&new);
                source.write_running_snapshot();
                if std::env::var("PPGW_DEBIAN").as_deref() == Ok("1") {
                    let pending = "/var/lib/paopaogw/core-token.reload";
                    if std::fs::write(pending, sb_web::derive_secret(&new.web.password)).is_ok() {
                        let _ = std::fs::rename(pending, "/var/lib/paopaogw/core-token");
                    }
                }
                tracing::info!("config reloaded");
            }
            Err(e) => {
                tracing::warn!(?e, "reload failed; keep old config");
            }
        }
    }
}
