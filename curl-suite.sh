#!/usr/bin/env bash

while true; do
    printf '\n1) List connections\n2) Get connection\n3) Create connection\n4) Replace connection\n5) Delete connection\n0) Quit\n'
    IFS= read -r -p 'Choose: ' choice || break

    case "$choice" in
        1)
            curl -i http://localhost:8000/connection
            ;;
        2)
            IFS= read -r -p 'Name: ' name || break
            curl -i --url-query "name=$name" http://localhost:8000/connection
            ;;
        3)
            IFS= read -r -p 'Body JSON ({"name":"example","type":"local","path":"/data"}): ' body || break
            curl -i -X POST \
                -H 'Content-Type: application/json' -d "$body" \
                http://localhost:8000/connection
            ;;
        4)
            IFS= read -r -p 'Name: ' name || break
            IFS= read -r -p 'Body JSON ({"type":"local","path":"/data"}): ' body || break
            curl -i -X PUT --url-query "name=$name" \
                -H 'Content-Type: application/json' -d "$body" \
                http://localhost:8000/connection
            ;;
        5)
            IFS= read -r -p 'Name: ' name || break
            curl -i -X DELETE --url-query "name=$name" http://localhost:8000/connection
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