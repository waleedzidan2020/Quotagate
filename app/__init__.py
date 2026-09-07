# Install the robust traffic-shaping engine before app.main imports network.
# This keeps the existing network module/API stable while allowing shaping
# to be upgraded independently and verified on antiX.
from . import network as _network
from . import nft_comment_compat as _nft_comment_compat
from . import shaping as _shaping
from . import shaping_compat as _shaping_compat

# antiX nft requires rule comments containing ':' to stay quoted as nft string
# literals. Install the compatibility shim before any firewall rules are built.
_nft_comment_compat.install()

# antiX can ship an older iproute2 whose `tc ... show` text differs from newer
# builds. Install a tolerant verifier before the shaping engine is exposed.
_shaping_compat.install()
_network.shaping = _shaping.shaping
_network.clear_shaping = _shaping.clear

# Guest Mode activation is a session boundary: devices already associated when
# Guest Mode is enabled stay untouched; only later unassigned joiners become
# Guests and receive the configured quota/speed limits.
from . import guestmode as _guestmode
_guestmode.install()

# Per-user quota accounting is installed before the policy guard wrappers so
# db.update_user/create_user composition remains intact. It keeps historical
# device usage, attributes new deltas to the current user at accounting time,
# and performs event-driven quota firewall refreshes only on state changes.
from . import quota as _quota
_quota.install()

# Install policy guards after Guest Mode and quota accounting so the wrappers
# compose correctly:
# - applying user/device/Guest speed limits automatically enables shaping;
# - invalid placeholder MACs never become devices;
# - duplicate IP rows cannot generate conflicting tc/nft marks.
from . import shaping_policy as _shaping_policy
_shaping_policy.install()
