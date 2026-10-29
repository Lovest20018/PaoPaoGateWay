//! Metadata attached to the running core; hot updates keep its boot identity.
use crate::config::Config;
use sha2::{Digest, Sha256};
use std::sync::{Mutex, RwLock};

pub struct Identity {
    pub boot_id: String,
    config_revision: RwLock<String>,
    loaded_at: RwLock<f64>,
    restart_signature: Mutex<String>,
}

fn restart_signature(cfg: &Config) -> String {
    let mut fixed = cfg.clone();
    let defaults = Config::default();
    fixed.route = defaults.route;
    fixed.inbound.splice_copy = defaults.inbound.splice_copy;
    fixed.inbound.splice_threshold = defaults.inbound.splice_threshold;
    fixed.inbound.sniff_timeout = defaults.inbound.sniff_timeout;
    fixed.acl = defaults.acl;
    fixed.web.password = String::new();
    fixed.web.admin_cidr = defaults.web.admin_cidr;
    fixed.inbound_proxy.auth = None;
    if let Some(stats) = &mut fixed.stats { stats.max_rec = 0; stats.net_cleanday = 0; }
    hex::encode(Sha256::digest(format!("{fixed:?}").as_bytes()))
}

impl Identity {
    pub fn new(cfg: &Config) -> Self {
        let boot_id = std::fs::read_to_string("/proc/sys/kernel/random/uuid")
            .expect("Linux random UUID source unavailable").trim().to_string();
        let value = Self { boot_id, config_revision: RwLock::new(String::new()),
            loaded_at: RwLock::new(0.0), restart_signature: Mutex::new(restart_signature(cfg)) };
        value.record(cfg); value
    }
    pub fn permits_hot_reload(&self, cfg: &Config) -> bool {
        *self.restart_signature.lock().unwrap_or_else(|e| e.into_inner()) == restart_signature(cfg)
    }
    pub fn record(&self, cfg: &Config) {
        let key = std::env::var("PPGW_REVISION_KEY").unwrap_or_else(|_| self.boot_id.clone());
        let mut hash = Sha256::new();
        hash.update(key.as_bytes()); hash.update([0]); hash.update(format!("{cfg:?}").as_bytes());
        *self.config_revision.write().unwrap_or_else(|e| e.into_inner()) = hex::encode(hash.finalize());
        *self.loaded_at.write().unwrap_or_else(|e| e.into_inner()) = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs_f64();
    }
    pub fn revision(&self) -> String {
        self.config_revision.read().unwrap_or_else(|e| e.into_inner()).clone()
    }
    pub fn value(&self) -> serde_json::Value {
        let policy = std::fs::read("/var/lib/paopaogw/admin/policy-status.json").ok()
            .and_then(|raw| serde_json::from_slice::<serde_json::Value>(&raw).ok());
        serde_json::json!({"schema_version": 1, "boot_id": self.boot_id,
            "core_version": crate::SNIFFBOX_VERSION, "evidence_schema": 1,
            "build_commit": option_env!("PPGW_BUILD_COMMIT"),
            "config_revision": self.revision(),
            "loaded_at": *self.loaded_at.read().unwrap_or_else(|e| e.into_inner()),
            "configuration_state": "loaded", "last_load_result": "success",
            "reload_policy": "hot_safe_fields_else_restart", "revision_scope": "sniffbox_config",
            "mihomo_configuration": policy.map(|value| serde_json::json!({
                "status": value.get("status"), "source_revision": value.get("source_revision"),
                "policy_revision": value.get("policy_revision"), "effective_revision": value.get("effective_revision"),
                "loaded_at": value.get("loaded_at"), "revision_scope": "mihomo_source_policy_effective"
            }))})
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn safe_hot_fields_update_revision_without_replacing_boot_identity() {
        let original = Config::default(); let identity = Identity::new(&original);
        let old_revision = identity.revision(); let boot = identity.boot_id.clone();
        let mut changed = original.clone(); changed.route.block_quic = !changed.route.block_quic;
        assert!(identity.permits_hot_reload(&changed)); identity.record(&changed);
        assert_ne!(identity.revision(), old_revision); assert_eq!(identity.boot_id, boot);
        changed.inbound.udp = !changed.inbound.udp;
        assert!(!identity.permits_hot_reload(&changed));
    }
}
