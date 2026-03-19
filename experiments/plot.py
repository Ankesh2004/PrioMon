import sqlite3
import matplotlib.pyplot as plt
import os

# the actual database for this project
dbname = 'PrioMonDB.db'

class PrioMonDataDB:
    def __init__(self):
        # We assume the script is run from the experiments/ directory 
        # or root directory. If the db isn't found, try experiments/.
        db_path = dbname
        if not os.path.exists(db_path):
            db_path = os.path.join(os.path.dirname(__file__), dbname)
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.connection.cursor()

    def get_query_success_by_failure_rate(self):
        try:
            self.cursor.execute(
                "SELECT failure_percent, "
                "AVG(CASE WHEN success = 'True' OR success = '1' THEN 1 ELSE 0 END) "
                "FROM query GROUP BY failure_percent"
            )
            return self.cursor.fetchall()
        except Exception as e:
            print("Error DB Query (get_query_success_by_failure_rate): {}".format(e))
            return []

    def get_bandwidth_savings_over_time(self):
        try:
            self.cursor.execute(
                "SELECT round, SUM(metrics_sent), SUM(metrics_filtered) "
                "FROM round_metrics_stats "
                "GROUP BY round "
                "ORDER BY round"
            )
            return self.cursor.fetchall()
        except Exception as e:
            print("Error DB Query (get_bandwidth_savings_over_time): {}".format(e))
            return []

    def get_total_bandwidth_saved(self):
        try:
            self.cursor.execute(
                "SELECT SUM(metrics_sent), SUM(metrics_filtered) "
                "FROM round_metrics_stats"
            )
            return self.cursor.fetchone()
        except Exception as e:
            print("Error DB Query (get_total_bandwidth_saved): {}".format(e))
            return (0, 0)

    def get_transmissions_by_metric_type(self):
        try:
            self.cursor.execute(
                "SELECT metric_type, "
                "SUM(CASE WHEN was_sent = 1 THEN 1 ELSE 0 END) as sent, "
                "SUM(CASE WHEN was_sent = 0 THEN 1 ELSE 0 END) as filtered "
                "FROM metric_transmissions "
                "GROUP BY metric_type"
            )
            return self.cursor.fetchall()
        except Exception as e:
            print("Error DB Query (get_transmissions_by_metric_type): {}".format(e))
            return []


def plot_query_success_vs_failure_rate(db):
    data = db.get_query_success_by_failure_rate()
    if not data:
        print("No data for Query Success vs Failure Rate.")
        return
    failure_rates = [row[0] for row in data]
    success_rates = [row[1] for row in data]
    
    plt.figure(figsize=(10, 6))
    plt.plot(failure_rates, success_rates, marker='o', color='b')
    plt.xlabel('Failure Rate (%)')
    plt.ylabel('Query Success Rate')
    plt.title('Query Success vs Node Failure Rate')
    plt.grid(True)
    plt.savefig('query_success_vs_failure_rate.png')
    print("Saved query_success_vs_failure_rate.png")


def plot_bandwidth_savings_over_time(db):
    data = db.get_bandwidth_savings_over_time()
    if not data:
        print("No data for Bandwidth Savings Over Time.")
        return
        
    rounds = [row[0] for row in data]
    sent = [row[1] for row in data]
    filtered = [row[2] for row in data]
    
    plt.figure(figsize=(10, 6))
    plt.plot(rounds, sent, label='Metrics Sent (Bandwidth Used)', color='red', marker='o')
    plt.plot(rounds, filtered, label='Metrics Filtered (Bandwidth Saved)', color='green', marker='x')
    plt.xlabel('Gossip Round')
    plt.ylabel('Number of Metrics')
    plt.title('Metrics Transmission Over Time (Bandwidth Savings)')
    plt.legend()
    plt.grid(True)
    plt.savefig('bandwidth_savings_over_time.png')
    print("Saved bandwidth_savings_over_time.png")


def plot_total_bandwidth_saved(db):
    data = db.get_total_bandwidth_saved()
    if not data or data == (None, None) or (data[0] == 0 and data[1] == 0):
        print("No data for Total Bandwidth Saved Pie Chart.")
        return
        
    sent, filtered = data
    labels = ['Metrics Sent', 'Metrics Filtered (Saved)']
    sizes = [sent, filtered]
    colors = ['#ff9999','#66b3ff']
    
    plt.figure(figsize=(8, 8))
    plt.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90)
    plt.title('Total Metric Bandwidth Usage')
    plt.axis('equal')
    plt.savefig('total_bandwidth_saved.png')
    print("Saved total_bandwidth_saved.png")


def plot_transmissions_by_metric_type(db):
    data = db.get_transmissions_by_metric_type()
    if not data:
        print("No data for Transmissions By Metric Type.")
        return
        
    metrics = [row[0] for row in data]
    sent = [row[1] for row in data]
    filtered = [row[2] for row in data]
    
    x = range(len(metrics))
    width = 0.35
    
    plt.figure(figsize=(10, 6))
    plt.bar(x, sent, width, label='Sent', color='salmon')
    plt.bar([i + width for i in x], filtered, width, label='Filtered (Saved)', color='lightgreen')
    
    plt.xlabel('Metric Type')
    plt.ylabel('Count')
    plt.title('Transmission vs Filtering by Metric Type')
    plt.xticks([i + width/2 for i in x], metrics)
    plt.legend()
    plt.grid(axis='y')
    plt.savefig('transmissions_by_metric_type.png')
    print("Saved transmissions_by_metric_type.png")


if __name__ == '__main__':
    db = PrioMonDataDB()
    
    # Generate all plots
    plot_query_success_vs_failure_rate(db)
    plot_bandwidth_savings_over_time(db)
    plot_total_bandwidth_saved(db)
    plot_transmissions_by_metric_type(db)
    
    # We do a final plt.show() if running interactively, otherwise just save images.
    # plt.show()
    print("All plotting routines finished.")
