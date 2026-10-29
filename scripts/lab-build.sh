#!/bin/sh
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends clang libclang-dev cmake perl git g++ python3 python3-yaml python3-jsonschema nftables iproute2 procps systemd util-linux openvpn openssl
export RUSTUP_TOOLCHAIN="$(rustup default | cut -d' ' -f1)"
rustup component add rustfmt --toolchain "$RUSTUP_TOOLCHAIN"
cd /src/sniffbox
export CARGO_BUILD_JOBS=2
export SNIFFBOX_VERSION=${SNIFFBOX_VERSION:-0.2.0-debian}
export PPGW_BUILD_COMMIT
rustc --version
cargo test --locked --workspace
cargo build --locked --release -p sniffbox
