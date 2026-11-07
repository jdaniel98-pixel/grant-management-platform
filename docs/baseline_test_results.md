# Baseline test results (M0)

Recorded 24 October 2026, before any platform code was written.

## PI Tool (`src/pi_workspace/`), unchanged copy

- **Environment:** Python 3.13, with only the three pinned packages from `src/pi_workspace/requirements.txt` installed (streamlit 1.64.0, openai 3.14.1, pydantic 2.13.5).
- **Setup:** no `OPENAI_API_KEY`, `PILOT_MODE` off.
- **Command:** `cd src/pi_workspace && python -m unittest discover -s tests`

| Test file | Tests | Result |
| --- | --- | --- |
| `test_pilot_storage.py` | 6 | 6 pass |
| `test_research_notebooks.py` | 11 | 11 pass |
| `test_workspace_reminders.py` | 8 | 8 pass |
| `test_matching_reliability.py` | 5 | **Not run: the module fails to import** |

**Result: 25 of 30 pass. 5 don't run.**

### Why `test_matching_reliability.py` fails

The test file starts with `import httpx`. openai 3.14.1 no longer depends on the `httpx` package: it uses the renamed `httpx2` package. So `httpx` isn't installed by the pinned requirements, and the import fails with `ModuleNotFoundError: No module named 'httpx'`. This is an environment gap, not a logic failure.

As a diagnostic only, I re-ran the module with a temporary alias outside the project that maps `httpx` to `httpx2`. All 5 tests passed. No project file was changed.

**Options for later (needs the product owner's decision; the PI copy stays unchanged until then):**
- Change the test import to `httpx2`, in the adapted copy under `grant_platform`. Recommended, since the PI copy is a baseline.
- Or add a pinned `httpx` to the PI requirements.

Streamlit prints "missing ScriptRunContext" warnings during the UI tests. They are expected in bare mode and harmless.

## Grant platform (`src/grant_platform/`), M1

- **Command:** `PYTHONPATH=src python -m unittest discover -s tests`
- **Result:** 17 of 17 pass, offline, in under a second.

## Follow-up: fix verified in a test environment (24 October 2026)

Adding `httpx==0.28.1` (now in `requirements-dev.txt`) to the pinned packages gives **30 of 30 PI tests passing** and 17 of 17 platform tests. Not yet confirmed on the product owner's Mac.

## Local verification on the product owner's Mac (24 October 2026)

M0 and M1 **passed local verification**. Python 3.13.15, `.venv` built from `requirements.txt` plus `requirements-dev.txt`.

| Suite | Command | Result |
| --- | --- | --- |
| PI Tool | `PYTHONPATH=src/pi_workspace .venv/bin/python -m unittest discover -s src/pi_workspace/tests -p 'test_*.py' -v` | 30 of 30 pass |
| Grant platform (M1) | `PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | 17 of 17 pass |

## M2 results (24 October 2026, test environment; Mac verification pending)

- **Grant platform:** 45 of 45 pass offline in about 3 seconds (`PYTHONPATH=src:. python -m unittest discover -s tests -p 'test_*.py'`): M1's 17, plus 13 profile-service, 8 funding-need-service and 7 screen walkthrough tests.
- **PI Tool, with `requirements-dev.txt`:** 30 of 30 pass on 10 of 11 runs. One run reported a single error that I could not reproduce in 9 reruns (including cold bytecode cache and three suites running at once) and did not capture. Treat it as an unexplained flake to watch.
- **Mutation check:** making `missing_facts` return nothing makes 4 tests fail, so the tests do detect that regression.
- **Real browser smoke test:** the app was started with `streamlit run` against a seeded database and the profile and funding needs pages were loaded in Chromium.
