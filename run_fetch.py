"""Root runner for daily fetch and verification. Delegates to src.run_fetch.main()."""
import sys
from src.run_fetch import main

if __name__ == "__main__":
    sys.exit(main())
