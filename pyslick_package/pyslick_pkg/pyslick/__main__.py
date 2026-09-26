import os
import sys
from pyslick import main

if __name__ == "__main__":
    _rc = 0
    try:
        main()
    except SystemExit as _e:
        _rc = _e.code if isinstance(_e.code, int) else 1
    except Exception:
        import traceback
        traceback.print_exc()
        _rc = 1
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(_rc)
