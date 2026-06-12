# Custom runtime hook - patch pathlib to handle missing data files in frozen app
# This runs BEFORE the default pyi_rth_pkgres.py runtime hook
import sys
import pathlib
import builtins

_orig_open = builtins.open

def _patched_read_text(self, encoding=None, errors=None):
    try:
        return self.read_text(encoding, errors)
    except FileNotFoundError:
        # For jaraco text files in frozen app, return empty string if missing
        return ""

# Apply patch
pathlib.Path.read_text = _patched_read_text

# Now let the default runtime hooks run
import pyi_rth_pkgres