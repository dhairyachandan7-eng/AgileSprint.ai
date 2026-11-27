# AgileSprint.ai — Monte Carlo Sprint Risk & Velocity Predictor

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-green?logo=flask&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-1.26-013243?logo=numpy&logoColor=white)
![Chart.js](https://img.shields.io/badge/Chart.js-4.4-FF6384?logo=chartdotjs&logoColor=white)
![Deployed on Render](https://img.shields.io/badge/Deployed%20on-Render-46E3B7?logo=render&logoColor=white)

**Live Demo:** https://agilesprint-ai.onrender.com/

AgileSprint.ai is a sprint forecasting and risk diagnostics tool built for Agile project managers, scrum masters, and product operations leads. Instead of relying on static historical averages during sprint planning, it runs a 5,000-iteration Monte Carlo simulation in NumPy to model velocity variance, external dependency drag, and carryover debt—giving teams realistic P10, P50, and P90 delivery confidence intervals before they commit to a backlog.

![AgileSprint.ai Dashboard](screenshots/dashboard.png)

## Features

### Monte Carlo Delivery Forecasting
- Runs 5,000 randomized sprint trials per request using NumPy normal distributions bounded at zero.
- Calculates exact completion probability (`%` of simulated sprints meeting or exceeding committed story points).
- Outputs **P10 (Conservative)**, **P50 (Median)**, and **P90 (Optimistic)** throughput benchmarks.

### Jira & Linear CSV Auto-Import
- Parses exported sprint CSV files directly in server memory via Python's `csv` and `io.StringIO` modules.
- Automatically detects points columns (`Completed Points`, `Velocity`, `Story Points`) and computes sample mean and sample standard deviation (`ddof=1`).

### Rule-Based Bottleneck Diagnostics
- Flags **Scope Over-Commitment** when committed points exceed historical velocity by more than 10%.
- Flags **Dependency Drag** when 3 or more external team dependencies threaten sprint flow.
- Flags **Carryover Debt** and **Contributor Saturation** when points per contributor-day exceed `1.2`.

## Core Formulas & Simulation Logic

```python
# Adjusted Effective Velocity accounting for dependency drag and carryover debt
dependency_penalty = dependencies * 0.035
carryover_drag = (carryover_points / max(committed_points, 1)) * 0.15
effective_mean_velocity = historical_velocity * max(0.5, 1.0 - dependency_penalty - carryover_drag)

# 5,000-Iteration Monte Carlo Sampling
simulated_outputs = np.clip(
    rng.normal(loc=effective_mean_velocity, scale=max(velocity_std_dev, 1.0), size=5000),
    0, None
)
completion_probability = (np.sum(simulated_outputs >= committed_points) / 5000) * 100
