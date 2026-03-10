import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import re
import sys
from datetime import timedelta
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

def parse_duration_to_hours(duration_str):
    match = re.match(r'(\d+)h:(\d+)m:(\d+)s', duration_str)
    if match:
        h, m, s = map(int, match.groups())
        return float(h) + (float(m) / 60.0) + (float(s) / 3600.0)
    return 0.0

def format_y_axis(x, pos):
    hours = int(x)
    minutes = int((x - hours) * 60)
    return f"{hours}h {minutes:02d}m"

def main():
    parser = argparse.ArgumentParser(description="Plot Advanced CI Build Analytics")
    parser.add_argument('filename', help="Path to the file containing bash script output")
    parser.add_argument('-o', '--output', default='ci_analytics.svg', help="Output file name")
    parser.add_argument('-w', '--weeks', type=int, default=0, help="Number of past weeks to plot")
    parser.add_argument('-g', '--grouping', choices=['day', 'week'], default='day',
                        help="Group data by 'day' or 'week'")

    parser.add_argument('-m', '--mode', choices=['timeline', 'correlation'], default='timeline',
                        help="'timeline' shows distribution over time. 'correlation' maps volume vs speed.")
    parser.add_argument('-v', '--show-volume', action='store_true',
                        help="Adds a volume bar chart beneath the timeline (only applies to 'timeline' mode)")

    # --- NEW ARGUMENT: Cutoff Time ---
    parser.add_argument('--max-hours', type=float, default=24.0,
                        help="Maximum duration in hours to include. Filters out stuck builds (Default: 24.0)")

    args = parser.parse_args()

    # 1. Load Data
    data = []
    try:
        with open(args.filename, 'r') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3:
                    data.append({
                        'timestamp': pd.to_datetime(f"{parts[0]} {parts[1]}"),
                        'duration_hours': parse_duration_to_hours(parts[2])
                    })
    except FileNotFoundError:
        print(f"Error: File '{args.filename}' not found.")
        sys.exit(1)

    df = pd.DataFrame(data)
    if df.empty:
        print("No valid data found.")
        sys.exit(1)

    # 2. Apply Time & Duration Filters

    # Filter by timeline (weeks)
    if args.weeks > 0:
        cutoff_date = df['timestamp'].max() - timedelta(weeks=args.weeks)
        df = df[df['timestamp'] >= cutoff_date]

    # --- Apply the Max Hours Cutoff ---
    if args.max_hours > 0:
        original_count = len(df)
        df = df[df['duration_hours'] <= args.max_hours]
        filtered_count = original_count - len(df)
        if filtered_count > 0:
            print(f"Filtered out {filtered_count} outlier builds that took longer than {args.max_hours} hours.")

    if df.empty:
        print("Error: No data remaining after applying filters.")
        sys.exit(1)

    # 3. Apply Grouping Logic
    if args.grouping == 'week':
        df['time_bucket'] = (df['timestamp'] - pd.to_timedelta(df['timestamp'].dt.weekday, unit='D')).dt.date
        x_label_title = 'Week Commencing'
        label_formatter = lambda d, count: f"Mon {d.strftime('%d-%m-%y')}\n({count})" if not args.show_volume else f"Mon {d.strftime('%d-%m-%y')}"
    else:
        df['time_bucket'] = df['timestamp'].dt.date
        x_label_title = 'Date'
        label_formatter = lambda d, count: f"{d.strftime('%d-%m-%y')}\n({count})" if not args.show_volume else f"{d.strftime('%d-%m-%y')}"

    unique_buckets = sorted(df['time_bucket'].unique())
    bucket_counts = df.groupby('time_bucket').size()
    durations_per_bucket = [df[df['time_bucket'] == b]['duration_hours'].values for b in unique_buckets]

    title_suffix = f"(Last {args.weeks} Weeks)" if args.weeks > 0 else "(All Data)"

    # ==========================================
    # VISUALIZATION MODE 1: CORRELATION SCATTER
    # ==========================================
    if args.mode == 'correlation':
        fig, ax = plt.subplots(figsize=(10, 8))

        x_vals = [bucket_counts[b] for b in unique_buckets]
        y_vals = [np.median(dur) for dur in durations_per_bucket]

        ax.scatter(x_vals, y_vals, alpha=0.7, color='#1976D2', s=80, edgecolors='white', zorder=3)

        if len(x_vals) > 1:
            z = np.polyfit(x_vals, y_vals, 1)
            p = np.poly1d(z)
            x_line = np.linspace(min(x_vals), max(x_vals), 100)

            slope_mins = z[0] * 60
            ax.plot(x_line, p(x_line), "r--", linewidth=2, label=f'Trend (+{slope_mins:.1f} mins per build)', zorder=2)

        ax.set_xlabel(f'Total Builds per {args.grouping.capitalize()}', fontsize=12, fontweight='bold')
        ax.set_ylabel('Median Test Duration', fontsize=12, fontweight='bold')
        ax.yaxis.set_major_formatter(plt.FuncFormatter(format_y_axis))
        ax.set_title(f'Queue Bottleneck Check: Volume vs. Duration {title_suffix}\n(Capped at {args.max_hours}h max)', fontsize=14)

        ax.grid(True, linestyle='--', alpha=0.5)
        ax.legend()
        plt.tight_layout()
        plt.savefig(args.output, format='svg')
        print(f"Correlation Plot saved to: {args.output}")
        return

    # ==========================================
    # VISUALIZATION MODE 2: TIMELINE & SUBPLOT
    # ==========================================
    dynamic_width = max(14, len(unique_buckets) * 0.4)

    if args.show_volume:
        fig, (ax_box, ax_vol) = plt.subplots(2, 1, figsize=(dynamic_width, 11),
                                             gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
        plt.subplots_adjust(hspace=0.05)
    else:
        fig, ax_box = plt.subplots(figsize=(dynamic_width, 9))
        ax_vol = None

    bp = ax_box.boxplot(durations_per_bucket, positions=range(len(unique_buckets)),
                        patch_artist=True, showfliers=False, widths=0.5)

    for box in bp['boxes']: box.set(facecolor='#E8F5E9', edgecolor='#A5D6A7', alpha=0.6)
    for median in bp['medians']: median.set(color='#D32F2F', linewidth=2.5)
    for whisker in bp['whiskers']: whisker.set(color='#BDBDBD', linestyle='--')
    for cap in bp['caps']: cap.set_color('#BDBDBD')

    for i, bucket in enumerate(unique_buckets):
        y = df[df['time_bucket'] == bucket]['duration_hours'].values
        jitter_scale = 0.1 if args.grouping == 'week' else 0.08
        x = np.random.normal(loc=i, scale=jitter_scale, size=len(y))
        ax_box.scatter(x, y, alpha=0.5, color='#1976D2', edgecolors='white', linewidth=0.5, s=50, zorder=3)

    ax_box.yaxis.set_major_formatter(plt.FuncFormatter(format_y_axis))
    ax_box.set_ylabel('Test Duration', fontsize=12, fontweight='bold')

    # Automatically enforce a clean Y-Axis up to the max_hours or the highest actual data point
    max_y_in_data = df['duration_hours'].max()
    ax_box.set_ylim(bottom=0, top=min(args.max_hours, max_y_in_data) * 1.05) # Add 5% padding to the top

    ax_box.set_title(f'CI Build Distribution {title_suffix}\n(Builds > {args.max_hours}h excluded)', fontsize=14)
    ax_box.grid(True, axis='y', linestyle='--', alpha=0.4)

    x_pos = range(len(unique_buckets))
    if ax_vol:
        y_counts = [bucket_counts[b] for b in unique_buckets]
        ax_vol.bar(x_pos, y_counts, color='#90CAF9', edgecolor='#1E88E5', alpha=0.8)
        ax_vol.set_ylabel('Build Volume', fontsize=12, fontweight='bold')
        ax_vol.grid(True, axis='y', linestyle='--', alpha=0.4)

        ax_vol.set_xticks(x_pos)
        labels = [label_formatter(b, bucket_counts[b]) for b in unique_buckets]
        ax_vol.set_xticklabels(labels, rotation=65, ha='right', fontsize=10, rotation_mode='anchor')
        ax_vol.set_xlabel(x_label_title, fontsize=12, fontweight='bold')
        plt.setp(ax_box.get_xticklabels(), visible=False)
    else:
        ax_box.set_xticks(x_pos)
        labels = [label_formatter(b, bucket_counts[b]) for b in unique_buckets]
        ax_box.set_xticklabels(labels, rotation=65, ha='right', fontsize=10, rotation_mode='anchor')
        ax_box.set_xlabel(x_label_title, fontsize=12, fontweight='bold')

    legend_elements = [
        Line2D([0], [0], marker='o', color='w', label='Individual Build', markerfacecolor='#1976D2', markersize=8),
        Line2D([0], [0], color='#D32F2F', lw=2.5, label='Median Time'),
        Patch(facecolor='#E8F5E9', edgecolor='#A5D6A7', label='Middle 50%')
    ]
    ax_box.legend(handles=legend_elements, loc='upper right')

    plt.tight_layout()
    plt.savefig(args.output, format='svg', bbox_inches='tight')
    print(f"Plot saved to: {args.output}")

if __name__ == "__main__":
    main()
