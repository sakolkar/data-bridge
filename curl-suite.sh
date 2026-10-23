#!/usr/bin/env bash

base_url=${DATABRIDGE_URL:-http://localhost:8000}

read_file_target() {
    IFS= read -r -p 'Connection name: ' name || return 1
    IFS= read -r -p 'File path: ' path || return 1
}

write_file() {
    local method=$1
    read_file_target || return 1
    IFS= read -r -p 'Local source file: ' source || return 1
    if [[ ! -f $source ]]; then
        echo 'Source file not found.'
        return 0
    fi
    curl -i -X "$method" --url-query "name=$name" --url-query 'action=write' \
        --url-query "path=$path" -H 'Content-Type: application/octet-stream' \
        --data-binary "@$source" "$base_url/connection/file"
}

while true; do
    printf '\nConnections\n'
    printf '1) List connections\n2) Get connection\n3) Create connection\n'
    printf '4) Replace connection\n5) Delete connection\n'
    printf '\nFiles\n'
    printf '6) List files\n7) Read file\n8) Head file\n9) Create file\n10) Replace file\n'
    printf '\n0) Quit\n'
    IFS= read -r -p 'Choose: ' choice || break

    case "$choice" in
        1)
            curl -i "$base_url/connection"
            ;;
        2)
            IFS= read -r -p 'Name: ' name || break
            curl -i --url-query "name=$name" "$base_url/connection"
            ;;
        3)
            IFS= read -r -p 'Body JSON ({"name":"example","type":"local","path":"/data"}): ' body || break
            curl -i -X POST \
                -H 'Content-Type: application/json' -d "$body" \
                "$base_url/connection"
            ;;
        4)
            IFS= read -r -p 'Name: ' name || break
            IFS= read -r -p 'Body JSON ({"type":"local","path":"/data"}): ' body || break
            curl -i -X PUT --url-query "name=$name" \
                -H 'Content-Type: application/json' -d "$body" \
                "$base_url/connection"
            ;;
        5)
            IFS= read -r -p 'Name: ' name || break
            curl -i -X DELETE --url-query "name=$name" "$base_url/connection"
            ;;
        6)
            IFS= read -r -p 'Connection name: ' name || break
            IFS= read -r -p 'Recursive? [y/N]: ' recursive || break
            [[ $recursive == [yY]* ]] && recursive=true || recursive=false
            curl -i --url-query "name=$name" --url-query 'action=list' \
                --url-query "recursive=$recursive" "$base_url/connection/file"
            ;;
        7)
            read_file_target || break
            IFS= read -r -p 'Save to local file (blank to print): ' output || break
            output_args=(-i)
            [[ -n $output ]] && output_args=(--output "$output" -w 'HTTP %{http_code}\n')
            curl "${output_args[@]}" --url-query "name=$name" --url-query 'action=read' \
                --url-query "path=$path" "$base_url/connection/file"
            ;;
        8)
            read_file_target || break
            IFS= read -r -p 'Lines (blank for 10): ' lines || break
            IFS= read -r -p 'Max bytes (blank for 65536): ' max_bytes || break
            limit_args=()
            [[ -n $lines ]] && limit_args+=(--url-query "lines=$lines")
            [[ -n $max_bytes ]] && limit_args+=(--url-query "max_bytes=$max_bytes")
            curl -i --url-query "name=$name" --url-query 'action=head' \
                --url-query "path=$path" "${limit_args[@]}" "$base_url/connection/file"
            ;;
        9)
            write_file POST || break
            ;;
        10)
            write_file PUT || break
            ;;
        0)
            break
            ;;
        *)
            echo 'Invalid option.'
            ;;
    esac
    printf '\n'
done
