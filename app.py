import csv
import io
import os
import numpy as np
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)


def parse_sprint_csv(file_stream) -> dict:
    """
    Parses a Jira/Linear/custom sprint CSV in memory to extract historical
    velocity mean and standard deviation without storing user data.
    """
    raw_text = file_stream.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw_text))

    if not reader.fieldnames:
        raise ValueError("CSV file appears empty or missing header columns.")

    # Match common Jira, Linear, or custom velocity column names
    target_keywords = [
        "completed",
        "velocity",
        "delivered",
        "story points",
        "story_points",
        "points",
    ]
    matched_col = None
    for col in reader.fieldnames:
        col_lower = col.strip().lower()
        if any(key in col_lower for key in target_keywords):
            matched_col = col
            break

    if not matched_col:
        raise ValueError(
            "Could not find a points column. Include a header like 'Completed Points' or 'Velocity'."
        )

    velocities = []
    for row in reader:
        val = row.get(matched_col, "").strip()
        if not val:
            continue
        try:
            num = float(val)
            if num >= 0:
                velocities.append(num)
        except ValueError:
            continue

    if len(velocities) < 2:
        raise ValueError(
            "CSV must contain at least 2 sprints with numeric story points to calculate variance."
        )

    arr = np.array(velocities, dtype=float)
    mean_vel = round(float(np.mean(arr)), 1)
    std_vel = round(max(0.5, float(np.std(arr, ddof=1))), 1)

    return {
        "sprints_analyzed": len(velocities),
        "column_used": matched_col,
        "historical_velocity": mean_vel,
        "velocity_std_dev": std_vel,
        "recent_velocities": velocities[-6:],
    }


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

    dependency_penalty = dependencies * 0.035
    carryover_drag = (carryover_points / max(committed_points, 1)) * 0.15
    effective_mean_velocity = historical_velocity * max(
        0.5, 1.0 - dependency_penalty - carryover_drag
    )

    simulated_outputs = rng.normal(
        loc=effective_mean_velocity,
        scale=max(velocity_std_dev, 1.0),
        size=simulations,
    )
    simulated_outputs = np.clip(simulated_outputs, 0, None)

    success_count = np.sum(simulated_outputs >= committed_points)
    completion_probability = round(float((success_count / simulations) * 100), 1)

    p10 = round(float(np.percentile(simulated_outputs, 10)), 1)
    p50 = round(float(np.percentile(simulated_outputs, 50)), 1)
    p90 = round(float(np.percentile(simulated_outputs, 90)), 1)

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


@app.route("/api/upload-csv", methods=["POST"])
def upload_csv():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No CSV file uploaded."}), 400
    uploaded_file = request.files["file"]
    if not uploaded_file.filename:
        return jsonify({"status": "error", "message": "Empty filename."}), 400
    try:
        stats = parse_sprint_csv(uploaded_file.stream)
        return jsonify({"status": "success", "data": stats})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


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
