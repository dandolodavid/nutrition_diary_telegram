from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

METRICS = [
    ("kcal", "Kcal"),
    ("protein_g", "Proteine (g)"),
    ("carbs_g", "Carboidrati (g)"),
    ("fat_g", "Grassi (g)"),
]


def build_comparison_chart(actual: dict, target: dict | None, title: str) -> BytesIO:
    fig, axes = plt.subplots(1, 4, figsize=(11, 3.5))
    fig.suptitle(title)

    for ax, (key, label) in zip(axes, METRICS):
        actual_value = actual.get(key, 0)
        bars_labels = ["Attuale"]
        bars_values = [actual_value]
        colors = ["#4C72B0"]

        if target is not None:
            bars_labels.append("Target")
            bars_values.append(target.get(key, 0))
            colors.append("#DD8452")

        ax.bar(bars_labels, bars_values, color=colors)
        ax.set_title(label, fontsize=10)
        ax.tick_params(axis="x", labelsize=8)

    fig.tight_layout(rect=(0, 0, 1, 0.93))

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def build_weight_trend_chart(daily_points: list[tuple], weekly_points: list[tuple], title: str) -> BytesIO:
    """daily_points: [(date, weight_kg), ...]; weekly_points: [(week_start_date, media_kg), ...]."""
    fig, ax = plt.subplots(figsize=(8, 4))

    if daily_points:
        dates = [d for d, _ in daily_points]
        values = [v for _, v in daily_points]
        ax.plot(dates, values, "o", color="#4C72B0", alpha=0.4, markersize=4, label="Peso giornaliero")

    if weekly_points:
        week_dates = [d for d, _ in weekly_points]
        week_values = [v for _, v in weekly_points]
        ax.plot(week_dates, week_values, "-o", color="#DD8452", linewidth=2, markersize=6,
                 label="Media settimanale")

    ax.set_title(title)
    ax.set_ylabel("Peso (kg)")
    ax.legend(fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf
