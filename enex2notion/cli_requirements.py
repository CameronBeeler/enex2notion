"""Validate runtime dependencies and requirements."""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def validate_requirements():
    """Validate all required dependencies are installed.
    
    Checks all packages from requirements.txt and reports any missing.
    Exits with error if any required packages are missing.
    """
    logger.info("=" * 80)
    logger.info("DEPENDENCY VALIDATION")
    logger.info("=" * 80)
    
    # Read requirements.txt
    requirements_file = Path(__file__).parent.parent / "requirements.txt"
    
    if not requirements_file.exists():
        logger.warning(f"⚠ requirements.txt not found at {requirements_file}")
        logger.warning("  Skipping dependency validation")
        logger.info("=" * 80)
        return
    
    # Parse requirements
    required_packages = []
    with open(requirements_file, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            # Extract package name (before >=, ==, etc.)
            pkg_name = line.split('>=')[0].split('==')[0].split('<')[0].split('>')[0].strip()
            if pkg_name:
                required_packages.append((pkg_name, line))
    
    logger.info(f"Checking {len(required_packages)} required packages...")
    logger.info("")
    
    # Check each package
    missing_packages = []
    installed_packages = []
    
    for pkg_name, requirement_line in required_packages:
        try:
            # Try to import the package
            if pkg_name == 'notion-client':
                import notion_client
                version = getattr(notion_client, '__version__', 'unknown')
                installed_packages.append((pkg_name, version))
            elif pkg_name == 'beautifulsoup4':
                import bs4
                version = getattr(bs4, '__version__', 'unknown')
                installed_packages.append((pkg_name, version))
            elif pkg_name == 'python-dateutil':
                import dateutil
                version = getattr(dateutil, '__version__', 'unknown')
                installed_packages.append((pkg_name, version))
            elif pkg_name == 'requests':
                import requests
                version = requests.__version__
                installed_packages.append((pkg_name, version))
            elif pkg_name == 'w3lib':
                import w3lib
                version = getattr(w3lib, '__version__', 'unknown')
                installed_packages.append((pkg_name, version))
            elif pkg_name == 'tinycss2':
                import tinycss2
                version = tinycss2.__version__
                installed_packages.append((pkg_name, version))
            elif pkg_name == 'tqdm':
                import tqdm
                version = tqdm.__version__
                installed_packages.append((pkg_name, version))
            elif pkg_name == 'lxml':
                import lxml
                from lxml import etree
                version = etree.LXML_VERSION
                version_str = '.'.join(map(str, version))
                installed_packages.append((pkg_name, version_str))
            else:
                # Generic import attempt
                __import__(pkg_name.replace('-', '_'))
                installed_packages.append((pkg_name, 'installed'))
                
        except ImportError:
            missing_packages.append((pkg_name, requirement_line))
    
    # Print results
    if installed_packages:
        logger.info("✓ Installed packages:")
        for pkg_name, version in installed_packages:
            logger.info(f"  • {pkg_name:<25} {version}")
    
    if missing_packages:
        logger.error("")
        logger.error("✗ MISSING PACKAGES:")
        for pkg_name, requirement_line in missing_packages:
            logger.error(f"  • {pkg_name:<25} ({requirement_line})")
        logger.error("")
        logger.error("SOLUTION: Install missing packages")
        logger.error("  Run: pip install -r requirements.txt")
        logger.error("")
        logger.info("=" * 80)
        sys.exit(1)
    
    logger.info("")
    logger.info("✓ All required dependencies are installed")
    logger.info("=" * 80)


def check_optional_tools():
    """Check for optional external tools."""
    # Currently no optional tools are required
    pass


def validate_python_version():
    """Validate Python version meets minimum requirement."""
    min_version = (3, 12)
    current_version = sys.version_info[:2]
    
    logger.info("=" * 80)
    logger.info("PYTHON VERSION CHECK")
    logger.info("=" * 80)
    logger.info(f"Current Python: {sys.version.split()[0]} ({sys.executable})")
    logger.info(f"Required: Python {min_version[0]}.{min_version[1]}+")
    
    if current_version < min_version:
        logger.error("")
        logger.error(f"✗ Python {current_version[0]}.{current_version[1]} is too old")
        logger.error(f"  Minimum required: Python {min_version[0]}.{min_version[1]}")
        logger.error("")
        logger.error("SOLUTION: Upgrade Python")
        logger.error(f"  Current: {sys.executable}")
        logger.error("  Download: https://www.python.org/downloads/")
        logger.error("")
        logger.info("=" * 80)
        sys.exit(1)
    
    logger.info(f"✓ Python version is compatible")
    logger.info("=" * 80)
