Round ORS1 (task ors1, 4 Nov 2026): an online soak through the real relay (163.192.120.37:36900, relay_config.json), on
GitHub's standard runners. A push that changes ors1/plan.json (or the workflow) starts .github/workflows/ors1_soak.yml: one job
per entry in the plan, side by side. Each job builds Forge 9a6f776 from its public source (cached after the first run), then runs
tools/online_soak.py - host and guests on the runner, every connection out to the relay and back when "relay" is true, or on
127.0.0.1 when it is false (the comparison). Only online_summary.txt, online_results.json and a few run facts come back, as a
commit under ors1_drop/<run id>/<job>/. No recordings, engine logs, passwords or room keys are uploaded. A relay job first
probes the relay (tools/relay_probe.py, 3 tries a minute apart) and stops with status "relay_down" if it doesn't answer.
