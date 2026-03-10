#./get_gerrit_comments.sh  > output.txt

mkdir -p ./results/

python3 plot_ci_stats.py output.txt -w 54 -o ./results/weekly_54w.svg --grouping week --show-volume --max-hours 25
python3 plot_ci_stats.py output.txt -w 8 -o ./results/weekly_8w.svg --grouping week --show-volume
python3 plot_ci_stats.py output.txt -w 8 -o ./results/daily_8w.svg --show-volume
