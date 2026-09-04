# Install the robust traffic-shaping engine before app.main imports network.
# This keeps the existing network module/API stable while allowing shaping
# to be upgraded independently and verified on antiX.
from . import network as _network
from . import shaping as _shaping

_network.shaping = _shaping.shaping
_network.clear_shaping = _shaping.clear
