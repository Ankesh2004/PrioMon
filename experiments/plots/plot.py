import sqlite3
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import PercentFormatter
import os

db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'priomonDB.db')


def create_voi_bandwidth_plots():
    """
    Create plots visualizing the Value of Information (VOI) based bandwidth usage
    using the metric_transmissions and round_metrics_stats tables.
    """
    # Connect to the database
    conn = sqlite3.connect(db_path)
    
    # Query 1: Get aggregate stats by round
    metrics_stats_df = pd.read_sql("""
        SELECT round, 
               SUM(metrics_sent) as sent, 
               SUM(metrics_filtered) as filtered,
               SUM(metrics_sent + metrics_filtered) as total
        FROM round_metrics_stats
        GROUP BY round
        ORDER BY round
    """, conn)
    
    # Query 2: Get bandwidth usage by metric type
    metrics_by_type_df = pd.read_sql("""
        SELECT metric_type, 
               SUM(CASE WHEN was_sent = 1 THEN 1 ELSE 0 END) as sent_count,
               SUM(CASE WHEN was_sent = 0 THEN 1 ELSE 0 END) as filtered_count,
               COUNT(*) as total_count
        FROM metric_transmissions
        GROUP BY metric_type
    """, conn)
    
    # Query 3: Get bandwidth usage over time for different nodes
    node_metrics_df = pd.read_sql("""
        SELECT node_ip, round, 
               SUM(metrics_sent) as sent, 
               SUM(metrics_filtered) as filtered
        FROM round_metrics_stats
        GROUP BY node_ip, round
        ORDER BY node_ip, round
    """, conn)
    
    # Calculate bandwidth savings percentages
    if not metrics_stats_df.empty:
        metrics_stats_df['savings_pct'] = (metrics_stats_df['filtered'] / metrics_stats_df['total']) * 100
    
    # Close connection
    conn.close()
    
    # Create the plots
    
    # Plot 1: Overall VOI Bandwidth Usage
    if not metrics_stats_df.empty:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
        
        # Stacked area chart for sent vs filtered metrics
        rounds = metrics_stats_df['round']
        sent = metrics_stats_df['sent']
        filtered = metrics_stats_df['filtered']
        
        ax1.stackplot(rounds, sent, filtered, labels=['Sent Metrics', 'Filtered Metrics'],
                     colors=['royalblue', 'tomato'], alpha=0.7)
        ax1.set_ylabel('Number of Metrics', fontsize=14)
        ax1.set_title('VOI-Based Metric Transmission by Round', fontsize=16)
        ax1.legend(loc='upper left', fontsize=12)
        ax1.grid(alpha=0.3)
        
        # Line chart for bandwidth savings percentage
        ax2.plot(rounds, metrics_stats_df['savings_pct'], color='green', marker='o', linewidth=2)
        ax2.set_xlabel('Round Number', fontsize=14)
        ax2.set_ylabel('Bandwidth Savings (%)', fontsize=14)
        ax2.set_title('Percentage of Bandwidth Saved with VOI Filtering', fontsize=16)
        ax2.grid(alpha=0.3)
        ax2.yaxis.set_major_formatter(PercentFormatter())
        
        plt.tight_layout()
        plt.savefig('voi_bandwidth_usage.png')
        plt.savefig('voi_bandwidth_usage.pdf')
        plt.close()
    
    # Plot 2: Bandwidth Usage by Metric Type
    if not metrics_by_type_df.empty:
        fig, ax = plt.subplots(figsize=(14, 8))
        
        metrics = metrics_by_type_df['metric_type']
        sent = metrics_by_type_df['sent_count']
        filtered = metrics_by_type_df['filtered_count']
        
        # Calculate percentages
        sent_pct = sent / (sent + filtered) * 100
        filtered_pct = filtered / (sent + filtered) * 100
        
        # Bar width
        width = 0.35
        
        # Bar positions
        x = np.arange(len(metrics))
        
        # Create the bars
        sent_bars = ax.bar(x - width/2, sent_pct, width, label='Sent', color='royalblue')
        filtered_bars = ax.bar(x + width/2, filtered_pct, width, label='Filtered', color='tomato')
        
        # Add labels and formatting
        ax.set_xlabel('Metric Type', fontsize=14)
        ax.set_ylabel('Percentage (%)', fontsize=14)
        ax.set_title('VOI Metric Transmission Distribution by Type', fontsize=16)
        ax.set_xticks(x)
        ax.set_xticklabels(metrics, rotation=45, ha='right')
        ax.yaxis.set_major_formatter(PercentFormatter())
        ax.legend(fontsize=12)
        ax.grid(axis='y', alpha=0.3)
        
        # Add value labels on bars
        def add_labels(bars):
            for bar in bars:
                height = bar.get_height()
                ax.annotate(f'{height:.1f}%',
                            xy=(bar.get_x() + bar.get_width() / 2, height),
                            xytext=(0, 3),  # 3 points vertical offset
                            textcoords="offset points",
                            ha='center', va='bottom', fontsize=10)
        
        add_labels(sent_bars)
        add_labels(filtered_bars)
        
        plt.tight_layout()
        plt.savefig('voi_metrics_by_type.png')
        plt.savefig('voi_metrics_by_type.pdf')
        plt.close()
    
    # Plot 3: Cumulative Bandwidth Savings
    if not metrics_stats_df.empty:
        fig, ax = plt.subplots(figsize=(12, 6))
        
        # Calculate cumulative metrics
        metrics_stats_df['cum_sent'] = metrics_stats_df['sent'].cumsum()
        metrics_stats_df['cum_filtered'] = metrics_stats_df['filtered'].cumsum()
        metrics_stats_df['cum_total'] = metrics_stats_df['total'].cumsum()
        metrics_stats_df['cum_savings_pct'] = (metrics_stats_df['cum_filtered'] / metrics_stats_df['cum_total']) * 100
        
        # Plot cumulative sent and filtered
        ax.plot(metrics_stats_df['round'], metrics_stats_df['cum_sent'], 
                color='royalblue', marker='o', linewidth=2, label='Cumulative Sent')
        ax.plot(metrics_stats_df['round'], metrics_stats_df['cum_filtered'], 
                color='tomato', marker='s', linewidth=2, label='Cumulative Filtered')
        ax.plot(metrics_stats_df['round'], metrics_stats_df['cum_total'], 
                color='purple', marker='^', linewidth=2, label='Cumulative Total')
        
        # Add savings percentage line on secondary y-axis
        ax2 = ax.twinx()
        ax2.plot(metrics_stats_df['round'], metrics_stats_df['cum_savings_pct'], 
                 color='green', marker='d', linewidth=2, linestyle='--', label='Savings %')
        ax2.set_ylabel('Bandwidth Savings (%)', fontsize=14)
        ax2.yaxis.set_major_formatter(PercentFormatter())
        
        # Set labels and title
        ax.set_xlabel('Round', fontsize=14)
        ax.set_ylabel('Cumulative Metric Count', fontsize=14)
        ax.set_title('Cumulative VOI Bandwidth Usage and Savings', fontsize=16)
        
        # Combine legends
        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=12)
        
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig('voi_cumulative_bandwidth.png')
        plt.savefig('voi_cumulative_bandwidth.pdf')
        plt.close()
    
    # Plot 4: Node Comparison - VOI Effectiveness
    if not node_metrics_df.empty:
        # Get top 5 nodes by total message count
        top_nodes = node_metrics_df.groupby('node_ip')[['sent', 'filtered']].sum().reset_index()
        top_nodes['total'] = top_nodes['sent'] + top_nodes['filtered']
        top_nodes = top_nodes.sort_values('total', ascending=False).head(5)['node_ip'].tolist()
        
        # Filter for top nodes
        top_node_data = node_metrics_df[node_metrics_df['node_ip'].isin(top_nodes)]
        
        # Plot
        fig, ax = plt.subplots(figsize=(14, 8))
        
        for i, node in enumerate(top_nodes):
            node_data = top_node_data[top_node_data['node_ip'] == node]
            if not node_data.empty:
                total = node_data['sent'] + node_data['filtered']
                savings_pct = (node_data['filtered'] / total) * 100
                ax.plot(node_data['round'], savings_pct, marker='o', linewidth=2, 
                        label=f'Node {node}')
        
        ax.set_xlabel('Round', fontsize=14)
        ax.set_ylabel('Bandwidth Savings (%)', fontsize=14)
        ax.set_title('VOI Bandwidth Savings by Node', fontsize=16)
        ax.legend(fontsize=12, loc='best')
        ax.grid(alpha=0.3)
        ax.yaxis.set_major_formatter(PercentFormatter())
        
        plt.tight_layout()
        plt.savefig('voi_node_comparison.png')
        plt.savefig('voi_node_comparison.pdf')
        plt.close()
    
    print("VOI bandwidth usage plots created successfully!")


def create_battery_savings_plots():
    """
    Create plots visualizing the potential battery savings implications of VOI-based
    metric filtering. These plots estimate how reduced network transmissions
    translate to extended battery life.
    """
    # Connect to the database
    conn = sqlite3.connect(db_path)
    
    # Debug: Check if tables exist
    tables_df = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", conn)
    print(f"Available tables in database: {tables_df['name'].tolist()}")
    
    # Debug: Check row counts
    try:
        count_df = pd.read_sql("SELECT COUNT(*) FROM round_metrics_stats", conn)
        print(f"round_metrics_stats table has {count_df.iloc[0,0]} rows")
        
        count_df = pd.read_sql("SELECT COUNT(*) FROM metric_transmissions", conn)
        print(f"metric_transmissions table has {count_df.iloc[0,0]} rows")
    except Exception as e:
        print(f"Error checking table data: {e}")
    
    # Query 1: Get filtered metrics by round
    try:
        round_metrics_df = pd.read_sql("""
            SELECT round, 
                   SUM(metrics_sent) as sent, 
                   SUM(metrics_filtered) as filtered,
                   SUM(metrics_sent + metrics_filtered) as total
            FROM round_metrics_stats
            GROUP BY round
            ORDER BY round
        """, conn)
        print(f"Query returned {len(round_metrics_df)} rows")
    except Exception as e:
        print(f"Error executing query: {e}")
        round_metrics_df = pd.DataFrame()
    
    # Query 2: Get filtered metrics by node
    node_metrics_df = pd.read_sql("""
        SELECT node_ip, 
               SUM(metrics_sent) as sent, 
               SUM(metrics_filtered) as filtered,
               SUM(metrics_sent + metrics_filtered) as total
        FROM round_metrics_stats
        GROUP BY node_ip
        ORDER BY node_ip
    """, conn)
    
    # Query 3: Get filtered metrics by type
    type_metrics_df = pd.read_sql("""
        SELECT metric_type, 
               SUM(CASE WHEN was_sent = 1 THEN 1 ELSE 0 END) as sent_count,
               SUM(CASE WHEN was_sent = 0 THEN 1 ELSE 0 END) as filtered_count,
               COUNT(*) as total_count
        FROM metric_transmissions
        GROUP BY metric_type
    """, conn)
    
    # Close connection
    conn.close()
    
    # Constants for battery usage estimations
    # These would ideally be calibrated based on actual device measurements
    BATTERY_PER_TRANSMISSION_MWH = 0.5  # milliwatt-hours per transmission
    BASELINE_BATTERY_CAPACITY_MWH = 10000  # e.g., 10000 mWh for a typical device
    TRANSMISSION_FIXED_COST_MWH = 0.2  # fixed cost of powering radio
    
    # Plot 1: Battery Savings Over Time
    if not round_metrics_df.empty:
        fig, ax = plt.subplots(figsize=(12, 6))
        
        # Calculate cumulative metrics and estimated battery savings
        round_metrics_df['cum_filtered'] = round_metrics_df['filtered'].cumsum()
        
        # Simple model: battery saved = fixed cost + per-transmission cost
        round_metrics_df['battery_saved_mwh'] = round_metrics_df['cum_filtered'] * BATTERY_PER_TRANSMISSION_MWH
        
        # Calculate as percentage of total battery capacity
        round_metrics_df['battery_saved_pct'] = (round_metrics_df['battery_saved_mwh'] / 
                                                BASELINE_BATTERY_CAPACITY_MWH) * 100
        
        # Plot battery savings over rounds
        ax.plot(round_metrics_df['round'], round_metrics_df['battery_saved_mwh'], 
                color='green', marker='o', linewidth=2)
        
        # Add percentage on secondary y-axis
        ax2 = ax.twinx()
        ax2.plot(round_metrics_df['round'], round_metrics_df['battery_saved_pct'],
                 color='orange', linestyle='--', linewidth=2)
        
        # Labels and formatting
        ax.set_xlabel('Round', fontsize=14)
        ax.set_ylabel('Estimated Battery Savings (mWh)', fontsize=14)
        ax2.set_ylabel('Battery Savings (% of capacity)', fontsize=14)
        ax.set_title('Estimated Battery Savings from VOI Filtering Over Time', fontsize=16)
        ax.grid(alpha=0.3)
        
        plt.tight_layout()
        plt.savefig('battery_savings_over_time.png')
        plt.savefig('battery_savings_over_time.pdf')
        plt.close()
    
    # Plot 2: Projected Battery Life Extension
    if not round_metrics_df.empty:
        # Calculate total filtering ratio
        total_sent = round_metrics_df['sent'].sum()
        total_filtered = round_metrics_df['filtered'].sum()
        total_metrics = total_sent + total_filtered
        filtering_ratio = total_filtered / total_metrics if total_metrics > 0 else 0
        
        # Create projected battery life scenarios based on filtering ratio
        standard_hours = 24  # baseline battery life without filtering
        extended_hours = standard_hours / (1 - (filtering_ratio * 0.7))  # 70% of filtering translates to battery extension
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        # Create bar chart comparing standard vs extended battery life
        scenarios = ['Without VOI Filtering', 'With VOI Filtering']
        battery_life = [standard_hours, extended_hours]
        
        bars = ax.bar(scenarios, battery_life, color=['lightgray', 'green'], width=0.6)
        
        # Add value labels
        for bar in bars:
            height = bar.get_height()
            ax.annotate(f'{height:.1f} hours',
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3),
                        textcoords="offset points",
                        ha='center', va='bottom', fontsize=12)
        
        # Add improvement percentage
        improvement_pct = ((extended_hours - standard_hours) / standard_hours) * 100
        ax.annotate(f'+{improvement_pct:.1f}%',
                    xy=(1, standard_hours + (extended_hours - standard_hours)/2),
                    xytext=(10, 0),
                    textcoords="offset points",
                    ha='left', va='center', fontsize=14,
                    color='green', weight='bold',
                    arrowprops=dict(arrowstyle='->'))
        
        ax.set_ylabel('Projected Battery Life (hours)', fontsize=14)
        ax.set_title('Projected Battery Life Extension with VOI Filtering', fontsize=16)
        ax.grid(axis='y', alpha=0.3)
        
        plt.tight_layout()
        plt.savefig('battery_life_projection.png')
        plt.savefig('battery_life_projection.pdf')
        plt.close()
    
    # Plot 3: Battery Impact by Metric Type
    if not type_metrics_df.empty:
        fig, ax = plt.subplots(figsize=(14, 8))
        
        # Assume different metric types have different transmission costs
        # (e.g., CPU metrics might be smaller than memory dumps)
        metric_type_costs = {
            # Default cost multiplier is 1.0
            # Add specific multipliers for known metric types
        }
        
        # Calculate battery savings by metric type
        type_metrics_df['energy_cost'] = [
            metric_type_costs.get(metric_type, 1.0) * BATTERY_PER_TRANSMISSION_MWH
            for metric_type in type_metrics_df['metric_type']
        ]
        
        type_metrics_df['battery_saved_mwh'] = type_metrics_df['filtered_count'] * type_metrics_df['energy_cost']
        
        # Sort by battery savings
        type_metrics_df = type_metrics_df.sort_values('battery_saved_mwh', ascending=False)
        
        # Create bar chart
        ax.bar(type_metrics_df['metric_type'], type_metrics_df['battery_saved_mwh'], 
               color='green', alpha=0.7)
        
        # Add value labels
        for i, v in enumerate(type_metrics_df['battery_saved_mwh']):
            ax.text(i, v + 5, f'{v:.1f} mWh', ha='center', fontsize=10)
        
        ax.set_xlabel('Metric Type', fontsize=14)
        ax.set_ylabel('Estimated Battery Savings (mWh)', fontsize=14)
        ax.set_title('Battery Savings by Metric Type', fontsize=16)
        plt.xticks(rotation=45, ha='right')
        ax.grid(axis='y', alpha=0.3)
        
        plt.tight_layout()
        plt.savefig('battery_savings_by_metric_type.png')
        plt.savefig('battery_savings_by_metric_type.pdf')
        plt.close()
    
    # Plot 4: Battery Savings Distribution Across Nodes
    if not node_metrics_df.empty:
        # Calculate battery savings for each node
        node_metrics_df['filtering_ratio'] = node_metrics_df['filtered'] / node_metrics_df['total']
        node_metrics_df['battery_saved_mwh'] = node_metrics_df['filtered'] * BATTERY_PER_TRANSMISSION_MWH
        
        # Sort by battery savings
        node_metrics_df = node_metrics_df.sort_values('battery_saved_mwh', ascending=False).head(10)
        
        fig, ax = plt.subplots(figsize=(14, 8))
        
        # Create horizontal bar chart
        bars = ax.barh(node_metrics_df['node_ip'], node_metrics_df['battery_saved_mwh'], 
                color='green', alpha=0.7)
        
        # Add filtering ratio as text
        for i, (idx, row) in enumerate(node_metrics_df.iterrows()):
            ax.text(row['battery_saved_mwh'] + 5, i, 
                    f'Filter ratio: {row["filtering_ratio"]:.2f}', 
                    va='center', fontsize=10)
        
        ax.set_xlabel('Estimated Battery Savings (mWh)', fontsize=14)
        ax.set_ylabel('Node IP', fontsize=14)
        ax.set_title('Top 10 Nodes by Battery Savings', fontsize=16)
        ax.grid(axis='x', alpha=0.3)
        
        plt.tight_layout()
        plt.savefig('battery_savings_by_node.png')
        plt.savefig('battery_savings_by_node.pdf')
        plt.close()
    
    print("Battery savings plots created successfully!")

if __name__ == "__main__":
    create_voi_bandwidth_plots()
    create_battery_savings_plots()  
