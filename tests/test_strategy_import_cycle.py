import subprocess
import sys


def test_strategy_d_backtest_imports_without_strategy_package_cycle():
    """Standalone Strategy D module import must not initialize the service graph."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import services.historical.strategy_d_sr_momentum_backtest",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_strategy_package_public_service_export_remains_available():
    """Lazy loading preserves the existing package-level public API."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from services.strategy import StrategyRepository, StrategyService; "
                "assert StrategyRepository is not None; assert StrategyService is not None"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
