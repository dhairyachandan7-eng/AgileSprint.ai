import csv
import io
import os
import numpy as np
from flask import Flask, render_template, request, jsonify, Response

app = Flask(__name__)


def run_monte_carlo(
    committed_points: float,
    historical_velocity: float,
    velocity_std_dev: float,
    sprint_days: int,
    team_size: int,
    dependencies: int,
    carryover_points: float,
    iterations: int = 5000,
) -> dict:
    rng = np.random.default_rng()

    # Apply penalty factors for cross-team dependencies and carryover debt
    dependency_penalty = dependencies * 0.035
    carryover_drag = (carryover_points / max(committed_points, 1.0)) * 0.15
    effective_mean = historical_velocity * max(0.5, 1.0 - dependency_penalty - carryover_drag)

    # Sample 5,000 sprint outcomes
    simulated_points = np.clip(
        rng.normal(loc=effective_mean, scale=max(velocity_std_dev, 1.0), size=iterations),
        0,
        None,
    )

    completion_prob = float(np.mean(simulated_points >= committed_points) * 100)
    p10, p50, p90 = np.percentile(simulated_points, [10, 50, 90])

    # Align Sprint Risk Score directly with failure probability + operational drag
    failure_prob = 100.0 - completion_prob
    capacity_days = max(sprint_days * team_size, 1)
    points_per_person_day = committed_points / capacity_days
    overcommit_ratio = committed_points / max(effective_mean, 1.0)

    raw_risk = (
        (failure_prob * 0.65)
        + min(dependencies * 4.5, 20.0)
        + min(max(overcommit_ratio - 1.0, 0.0) * 35.0, 15.0)
    )
    risk_score = int(np.clip(round(raw_risk), 5, 99))

    if risk_score >= 70:
        risk_label = "High Risk"
    elif risk_score >= 40:
        risk_label = "Moderate Risk"
    else:
        risk_label = "Low Risk"

    # Build histogram bins
    counts, bin_edges = np.histogram(simulated_points, bins=16)
    labels = [f"{int(bin_edges[i])}-{int(bin_edges[i+1])}" for i in range(len(counts))]
    bin_midpoints = [(bin_edges[i] + bin_edges[i + 1]) / 2.0 for i in range(len(counts))]

    # Diagnostics & Mitigations
    bottlenecks = []
    mitigations = []

    if committed_points > effective_mean:
        gap = round(committed_points - effective_mean, 1)
        bottlenecks.append(
            f"Scope Over-Commitment: Planned scope ({int(committed_points)} pts) exceeds adjusted effective velocity ({round(effective_mean, 1)} pts) by {gap} pts."
        )
        mitigations.append(
            f"Defer {int(round(gap))} points of lower-priority stretch stories to bring scope in line with effective capacity (~{int(round(effective_mean))} pts)."
        )

    if dependencies >= 3:
        bottlenecks.append(
            f"External Dependency Drag: {dependencies} cross-team blockers reduce effective throughput by ~{round(dependency_penalty * 100, 1)}%."
        )
        mitigations.append(
            "Resolve or decouple external API/team blockers before sprint kickoff, or move dependent tickets to the next sprint."
        )

    if carryover_points > 0:
        bottlenecks.append(
            f"Carryover Debt: {int(carryover_points)} unfinished points from the prior sprint absorb active development capacity."
        )
        mitigations.append(
            "Cap carryover intake and split stories larger than 8 points into smaller deliverable sub-tasks."
        )

    if points_per_person_day > 1.2:
        bottlenecks.append(
            f"Contributor Saturation: Workload averages {round(points_per_person_day, 2)} pts/person-day (above the 1.2 sustainable threshold)."
        )
        mitigations.append(
            "Rebalance ticket assignments across team members or add a 15% buffer for code review and QA."
        )

    if not bottlenecks:
        bottlenecks.append("Balanced Sprint Load: Committed scope aligns well with adjusted historical capacity.")
        mitigations.append("Maintain current WIP limits and track daily burndown against the P50 median pace.")

    return {
        "kpis": {
            "completion_probability": round(completion_prob, 1),
            "risk_score": risk_score,
            "risk_label": risk_label,
            "effective_velocity": round(effective_mean, 1),
            "p10": round(float(p10), 1),
            "p50": round(float(p50), 1),
            "p90": round(float(p90), 1),
            "committed_points": committed_points,
        },
        "histogram": {
            "labels": labels,
            "counts": counts.tolist(),
            "midpoints": bin_midpoints,
        },
        "diagnostics": {
            "bottlenecks": bottlenecks,
            "mitigations": mitigations,
        },
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/sample-csv", methods=["GET"])
def download_sample_csv():
    sample_content = (
        "Sprint,Completed Points,Carryover Points,Dependencies\n"
        "Sprint 21,34,4,2\n"
        "Sprint 22,38,2,1\n"
        "Sprint 23,31,6,3\n"
        "Sprint 24,36,3,2\n"
        "Sprint 25,35,2,1\n"
        "Sprint 26,33,5,2\n"
    )
    return Response(
        sample_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=sample_jira_velocity.csv"},
    )


@app.route("/api/upload-csv", methods=["POST"])
def upload_csv():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No CSV file uploaded."}), 400

    file_storage = request.files["file"]
    try:
        raw_text = file_storage.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw_text))
        points_history = []
        matched_column = "Completed Points"

        for row in reader:
            for col in ("Completed Points", "Delivered Points", "Velocity", "Story Points", "completed_points"):
                if col in row and row[col] and row[col].strip():
                    points_history.append(float(row[col].strip()))
                    matched_column = col
                    break

        if not points_history:
            return jsonify({
                "status": "error",
                "message": "Could not find a 'Completed Points' or 'Velocity' column in the CSV."
            }), 400

        mean_vel = float(np.mean(points_history))
        std_vel = float(np.std(points_history, ddof=1)) if len(points_history) > 1 else round(mean_vel * 0.15, 2)

        return jsonify({
            "status": "success",
            "data": {
                "sprints_analyzed": len(points_history),
                "column_used": matched_column,
                "mean_velocity": round(mean_vel, 1),
                "std_dev": round(std_vel, 2),
            },
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": f"CSV parse error: {str(exc)}"}), 400


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True)
    try:
        result = run_monte_carlo(
            committed_points=float(data.get("committed_points", 44)),
            historical_velocity=float(data.get("historical_velocity", 38)),
            velocity_std_dev=float(data.get("velocity_std_dev", 5)),
            sprint_days=int(data.get("sprint_days", 10)),
            team_size=int(data.get("team_size", 5)),
            dependencies=int(data.get("dependencies", 3)),
            carryover_points=float(data.get("carryover_points", 6)),
        )
        return jsonify({"status": "success", "data": result})
    except (ValueError, TypeError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
