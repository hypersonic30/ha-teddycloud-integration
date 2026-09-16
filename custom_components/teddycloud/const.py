"""Constants for the TeddyCloud integration."""

DOMAIN = "teddycloud"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_SSL = "ssl"
CONF_VERIFY_SSL = "verify_ssl"
# Base URL of an optional teddycloud-nfc-bridge sidecar (see
# https://github.com/hypersonic30/teddycloud-nfc-bridge). Empty string means
# the assign_nfc_tag service is unavailable for this entry.
CONF_SIDECAR_URL = "sidecar_url"

# Optional GitHub repo (owner/repo) holding a backup set of .nfc tag dumps.
# When set, those files are offered as importable "backup" entries in the
# wishlist (see wishlist.py / github_nfc_source.py) — empty means the
# feature is unavailable for this entry, same as CONF_SIDECAR_URL.
CONF_GITHUB_REPO = "github_repo"
CONF_GITHUB_BRANCH = "github_branch"
CONF_GITHUB_PATH = "github_path"
CONF_GITHUB_TOKEN = "github_token"
# How often (minutes) to re-check un-acquired wishlist items against the
# GitHub repo - see coordinator.py's _maybe_import_matching_backups.
CONF_GITHUB_CHECK_INTERVAL = "github_check_interval"

DEFAULT_PORT = 7780
DEFAULT_SSL = False
DEFAULT_VERIFY_SSL = True
DEFAULT_GITHUB_BRANCH = "main"
DEFAULT_GITHUB_CHECK_INTERVAL = 10

REQUEST_TIMEOUT = 8
UPDATE_INTERVAL = 20

# "internal.*" settings are not listed by /api/settings/getIndex but are
# readable directly via /api/settings/get/<key> — verified live against a
# teddyCloud server (2026-08).
SETTING_ONLINE = "internal.online"
SETTING_LAST_CONNECTION = "internal.last_connection"
SETTING_LAST_RUID = "internal.last_ruid"
SETTING_IP = "internal.ip"

# These *are* listed by getIndex (with correctly-typed JSON values), so the
# coordinator reads them all in one call instead of one request per key.
SETTING_CLOUD_ENABLED = "cloud.enabled"
SETTING_CLOUD_CACHE_CONTENT = "cloud.cacheContent"
SETTING_MAX_VOL_SPK = "toniebox.max_vol_spk"
SETTING_MAX_VOL_HDP = "toniebox.max_vol_hdp"
SETTING_LED = "toniebox.led"
SETTING_SLAP_ENABLED = "toniebox.slap_enabled"
SETTING_SLAP_BACK_LEFT = "toniebox.slap_back_left"

# toniebox.max_vol_spk / toniebox.max_vol_hdp, per getIndex's own description
# ("0=25%, 1=50%, 2=75%, 3=100%").
VOLUME_OPTIONS = {"0": "25%", "1": "50%", "2": "75%", "3": "100%"}

# toniebox.led, per getIndex's own description ("0=on, 1=off, 2=dimmed").
LED_OPTIONS = {"0": "on", "1": "off", "2": "dimmed"}
