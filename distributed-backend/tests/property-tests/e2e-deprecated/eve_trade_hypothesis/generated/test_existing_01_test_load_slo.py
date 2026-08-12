from eve_trade_hypothesis.registry import register_existing

NATIVE_FILE = 'test_load_slo.py'
CONTRACT_NAMES = ['test_authenticated_udp_issue_burst_meets_slo_and_preserves_state']

register_existing(globals(), NATIVE_FILE, CONTRACT_NAMES)
