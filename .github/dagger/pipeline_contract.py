"""Backward-compatible entry point for the stronger reliability contract."""
from reliability_contract import run
from _common import stage
if __name__ == "__main__": stage("pipeline-contract",run)
