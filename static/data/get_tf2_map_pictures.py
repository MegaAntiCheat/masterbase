from html import parser

import requests
from bs4 import BeautifulSoup


class MapInfo:
    map_name: str = None


def main():
    with open('./map-wiki.html', 'r') as h:
        data_html = h.read()

    parsed_html = BeautifulSoup(data_html, features="html.parser")
    table_rows = parsed_html.find_all('tr')
    rows = []
    for row in table_rows:
        row_data = []
        for col in row.contents:
            if col == '\n':
                continue

            _col_str = str(col)
            _col_str = _col_str.replace("<td>", "").replace("</td>", "")
            row_data.append(_col_str)
        rows.append(row_data)

    for row in rows:
        print('\n'.join(row))


if __name__ == "__main__":
    main()


