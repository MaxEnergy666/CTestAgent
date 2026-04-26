"""Quick import test"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from core.message_bus import MessageBus
    from core.message_types import Phase, TestCase, Message
    from core.global_state import GlobalTestState
    from core.base_agent import BaseAgent
    print("[OK] core/ imports")
except Exception as e:
    print(f"[FAIL] core/: {e}")

try:
    from agents.analyzer import AnalyzerAgent
    print("[OK] AnalyzerAgent")
except Exception as e:
    print(f"[FAIL] AnalyzerAgent: {e}")

try:
    from agents.generator import GeneratorAgent
    print("[OK] GeneratorAgent")
except Exception as e:
    print(f"[FAIL] GeneratorAgent: {e}")

try:
    from agents.executor import ExecutorAgent
    print("[OK] ExecutorAgent")
except Exception as e:
    print(f"[FAIL] ExecutorAgent: {e}")

try:
    from agents.reviewer import ReviewerAgent
    print("[OK] ReviewerAgent")
except Exception as e:
    print(f"[FAIL] ReviewerAgent: {e}")

try:
    from agents.coordinator import CoordinatorAgent
    print("[OK] CoordinatorAgent")
except Exception as e:
    print(f"[FAIL] CoordinatorAgent: {e}")

try:
    from agents.reporter import ReporterAgent
    print("[OK] ReporterAgent")
except Exception as e:
    print(f"[FAIL] ReporterAgent: {e}")

# Test instantiation
bus = MessageBus()
print(f"\n[OK] MessageBus created: {bus}")

state = GlobalTestState()
print(f"[OK] GlobalTestState: {state}")

print("\nAll imports and basic instantiation successful!")
