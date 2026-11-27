import os
import numpy as np
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)


def run_sprint_simulation(
    committed_points: float,
    historical_velocity: float,
    velocity_std_dev: float,
    team_size: int,
    dependencies: int,
    carryover_points: float,
    sprint_days: int,
    simulations: int = 5000,
) -> dict:
    """
    Runs a stateless Monte Carlo simulation to predict sprint completion
    probability, bottleneck risks, and actionable mitigation steps.
    """
    rng = np.random.default_rng()

    # Adjust baseline velocity based on dependency drag and carryover overhead
    dependency_penalty = dependencies * 0.035  # 3.5% efficiency drag per external dependency
    carryover_drag = (carryover_points / max(committed_points, 1)) * 0.15
    effective_mean_velocity = historical_velocity * max(
        0.5, 1.0 - dependency_penalty - carryover_drag
    )

    # Simulate sprint delivery across N iterations
    simulated_outputs = rng.normal(
        loc=effective_mean_velocity,
        scale=max(velocity_std_dev, 1.0),
        size=simulations,
    )
    simulated_outputs = np.clip(simulated_outputs, 0, None)

    # Calculate probability of completing committed scope
    success_count = np.sum(simulated_outputs >= committed_points)
    completion_probability = round(float((success_count / simulations) * 100), 1)

    # Percentiles for forecasting
    p10 = round(float(np.percentile(simulated_outputs, 10)), 1)  # Pessimistic (90% confidence)
    p50 = round(float(np.percentile(simulated_outputs, 50)), 1)  # Most Likely
    p90 = round(float(np.percentile(simulated_outputs, 90)), 1)  # Optimistic

    # Compute Risk Score (0 to 100, higher = riskier)
    scope_ratio = committed_points / max(historical_velocity, 1.0)
    points_per_dev_day = committed_points / max(team_size * sprint_days, 1)

    risk_score = 0
    risk_score += min(45, max(0, (scope_ratio - 0.85) * 90))
    risk_score += min(25, dependencies * 5)
    risk_score += min(20, (carryover_points / max(committed_points, 1)) * 60)
    risk_score += 10 if points_per_dev_day > 1.2 else 0
    risk_score = round(min(100, max(5, risk_score)))

    if risk_score >= 70:
        risk_level = "High Risk"
    elif risk_score >= 40:
        risk_level = "Moderate Risk"
    else:
        risk_level = "Healthy"

    # Diagnose specific bottlenecks
    bottlenecks = []
    mitigations = []

    if scope_ratio > 1.1:
        excess = round(committed_points - historical_velocity, 1)
        bottlenecks.append(
            f"Over-commitment: Scope exceeds historical average velocity by {excess} story points."
        )
        mitigations.append(
            f"Move {excess} points of lowest-priority backlog items into a stretch goal bucket."
        )

    if dependencies >= 3:
        bottlenecks.append(
            f"Dependency Drag: {dependencies} external dependencies create blocking risk."
        )
        mitigations.append(
            "Schedule a mid-sprint cross-team sync and front-load dependent tickets in Days 1–3."
        )

    if carryover_points > 0:
        carryover_pct = round((carryover_points / max(committed_points, 1)) * 100, 1)
        bottlenecks.append(
            f"Carryover Debt: {carryover_pct}% of sprint capacity is consumed by unfinished prior work."
        )
        mitigations.append(
            "Audit carryover stories for hidden scope creep and break any story over 8 points into sub-tasks."
        )

    if points_per_dev_day > 1.2:
        bottlenecks.append(
            f"Capacity Saturation: {round(points_per_dev_day, 2)} points per contributor-day exceeds sustainable thresholds."
        )
        mitigations.append(
            "Protect core engineering focus blocks and defer non-critical meetings until sprint review."
        )

    if not bottlenecks:
        bottlenecks.append("No major structural bottlenecks detected in current sprint parameters.")
        mitigations.append(
            "Maintain daily standup pacing and track burndown variance at the sprint midpoint."
        )

    # Histogram buckets for frontend chart
    counts, bin_edges = np.histogram(simulated_outputs, bins=10)
    histogram = [
        {
            "range": f"{round(float(bin_edges[i]))}-{round(float(bin_edges[i+1]))}",
            "count": int(counts[i]),
        }
        for i in range(len(counts))
    ]

    return {
        "completion_probability": completion_probability,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "forecast": {"p10": p10, "p50": p50, "p90": p90},
        "effective_velocity": round(effective_mean_velocity, 1),
        "bottlenecks": bottlenecks,
        "mitigations": mitigations,
        "histogram": histogram,
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True)
    try:
        result = run_sprint_simulation(
            committed_points=float(data.get("committed_points", 40)),
            historical_velocity=float(data.get("historical_velocity", 36)),
            velocity_std_dev=float(data.get("velocity_std_dev", 5)),
            team_size=int(data.get("team_size", 5)),
            dependencies=int(data.get("dependencies", 2)),
            carryover_points=float(data.get("carryover_points", 4)),
            sprint_days=int(data.get("sprint_days", 10)),
        )
        return jsonify({"status": "success", "data": result})
    except (ValueError, TypeError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
