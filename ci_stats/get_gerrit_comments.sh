#!/bin/bash

# Configuration
GERRIT_BASE="https://review.spdk.io"
WEEKS=54
PAGE_SIZE=500

# 1. OS-Agnostic Date Setup
# We need to do date math (subtraction) for the older builds.
# GNU/Linux and macOS/BSD handle date string conversion differently.
if date -v -${WEEKS}w > /dev/null 2>&1; then
    OS_TYPE="BSD"
    CUTOFF_DATE=$(date -v -${WEEKS}w "+%Y-%m-%d %H:%M:%S")
else
    OS_TYPE="GNU"
    CUTOFF_DATE=$(date -d "${WEEKS} weeks ago" "+%Y-%m-%d %H:%M:%S")
fi

# Helper function to convert "YYYY-MM-DD HH:MM:SS" to Epoch seconds
function get_epoch() {
    if [ "$OS_TYPE" = "BSD" ]; then
        date -j -f "%Y-%m-%d %H:%M:%S" "$1" +%s
    else
        date -d "$1" +%s
    fi
}

echo "Fetching data newer than $CUTOFF_DATE..." >&2
echo "Using $OS_TYPE date utilities for time calculation..." >&2

# Regex matchers
retrigger_regex="Results:.*[0-9]+/([0-9]+)"
duration_regex="Tests finished in ([0-9]+h:[0-9]+m:[0-9]+s) after"

batch_file=$(mktemp)
trap 'rm -f "$batch_file" "$batch_file.tmp"' EXIT

(
    for (( w=0; w<WEEKS; w++ )); do
        start_age=$w
        end_age=$((w+1))

        base_query="(status:open OR status:closed) AND comment:\"Build successful\""

        if [ "$start_age" -eq 0 ]; then
            time_query="${base_query} AND -age:${end_age}w"
        else
            time_query="${base_query} AND -age:${end_age}w AND age:${start_age}w"
        fi

        echo "  [Week $((w+1))/$WEEKS] Fetching..." >&2

        OFFSET=0
        MORE_CHANGES=true

        while [ "$MORE_CHANGES" = true ]; do

            # Fetch Data
            # ADDED: o=ALL_REVISIONS so we can see when each patch set was created
            http_code=$(curl -s -L -G -w "%{http_code}" -o "$batch_file" \
                "${GERRIT_BASE}/changes/" \
                --data-urlencode "q=${time_query}" \
                --data-urlencode "o=MESSAGES" \
                --data-urlencode "o=ALL_REVISIONS" \
                --data-urlencode "n=${PAGE_SIZE}" \
                --data-urlencode "S=${OFFSET}")

            if [ "$http_code" != "200" ]; then
                echo "Error: Server returned HTTP $http_code" >&2
                break
            fi

            if [ ! -s "$batch_file" ]; then break; fi

            first_line=$(head -n 1 "$batch_file")
            if [[ "$first_line" == ")]}'" ]]; then
                tail -n +2 "$batch_file" > "$batch_file.tmp" && mv "$batch_file.tmp" "$batch_file"
            fi

            if ! jq -e . >/dev/null 2>&1 < "$batch_file"; then break; fi

            count=$(jq 'length' < "$batch_file")
            if [ "$count" -eq 0 ]; then break; fi

            # JQ PARSING LOGIC:
            # We map .revisions into a lookup table ($ps_times) where Key = Patchset #, Value = Creation Date.
            # Then we extract the creation date for the specific patch set the comment belongs to.
            cat "$batch_file" | jq -r --arg cutoff "$CUTOFF_DATE" '
                .[] | . as $change |
                ($change.revisions | to_entries | map({key: (.value._number | tostring), value: .value.created}) | from_entries) as $ps_times |
                .messages[]? |
                select(.date >= $cutoff) |
                select(.message | contains("Build successful")) |
                (._revision_number | tostring) as $ps_num |
                $ps_times[$ps_num] as $ps_created |
                "\(.date)|\($change._number)|\(._revision_number)|\(.id)|\($ps_created)|\(.message | gsub("[\n\r]"; " "))"
            ' | while read -r line; do

                # Extract fields (dropping nanoseconds from dates)
                timestamp=$(echo "$line" | cut -d'|' -f1 | cut -d'.' -f1)
                change_num=$(echo "$line" | cut -d'|' -f2)
                patch_set=$(echo "$line" | cut -d'|' -f3)
                comment_id=$(echo "$line" | cut -d'|' -f4)
                ps_created=$(echo "$line" | cut -d'|' -f5 | cut -d'.' -f1)
                message=$(echo "$line" | cut -d'|' -f6-)

                # Filter Retriggers (for the newer log format)
                if [[ "$message" =~ $retrigger_regex ]]; then
                    retrigger_count="${BASH_REMATCH[1]}"
                    if [[ "$retrigger_count" != "1" ]]; then continue; fi
                fi

                duration=""

                # DURATION EXTRACTION LOGIC
                # Method 1: It's written in the comment (Newer patches)
                if [[ "$message" =~ $duration_regex ]]; then
                    duration="${BASH_REMATCH[1]}"

                # Method 2: Calculate it from timestamps (Older patches)
                elif [[ -n "$ps_created" && "$ps_created" != "null" ]]; then

                    # Convert dates to epoch seconds
                    t_start=$(get_epoch "$ps_created")
                    t_end=$(get_epoch "$timestamp")

                    if [[ -n "$t_start" && -n "$t_end" ]]; then
                        diff=$((t_end - t_start))

                        # Only proceed if difference is positive
                        if (( diff >= 0 )); then
                            h=$((diff / 3600))
                            m=$(((diff % 3600) / 60))
                            s=$((diff % 60))
                            # Format to match the exact string format expected by python (00h:00m:00s)
                            duration=$(printf "%02dh:%02dm:%02ds" $h $m $s)
                        fi
                    fi
                fi

                # If we successfully found or calculated a duration, output the line
                if [[ -n "$duration" ]]; then
                    link="${GERRIT_BASE}/c/${change_num}/${patch_set}#message-${comment_id}"
                    echo "$timestamp  $duration  $link"
                fi

            done

            if [ "$count" -lt "$PAGE_SIZE" ]; then
                MORE_CHANGES=false
            else
                OFFSET=$((OFFSET + count))
            fi

        done
    done
) | sort -u
