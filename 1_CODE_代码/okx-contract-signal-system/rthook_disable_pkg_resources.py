# Custom runtime hook - no pkg_resources
import sys

# Prevent pkg_resources from being imported at startup
def _patch_pkg_resources():
    import sys
    # Stub out pkg_resources to avoid the import error
    class _StubPkgResources:
        @staticmethod
        def require(*args, **kwargs):
            raise RuntimeError("pkg_resources disabled in frozen app")
        @staticmethod
        def working_set(*args, **kwargs):
            return []
        @staticmethod
        def Distribution(*args, **kwargs):
            raise RuntimeError("pkg_resources disabled in frozen app")
        @staticmethod
        def find_plugins(*args, **kwargs):
            return []
        @staticmethod
        def get_distribution(*args, **kwargs):
            return None
        @staticmethod
        def iter_entry_points(*args, **kwargs):
            return iter([])
        @staticmethod
        def run_script(*args, **kwargs):
            pass
        def __getattr__(self, name):
            return getattr(sys.modules[__name__], name, lambda: None)

    # Don't actually replace - let it load but patch the missing file issue
    pass

# Now import the rest of the app