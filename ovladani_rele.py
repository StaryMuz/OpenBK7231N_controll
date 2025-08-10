name: Ovladej WiFi relé podle cen OTE

on:
  schedule:
    - cron: "0 8-17 * 11-3 *"  # zimní čas CET = 9–18 ČR
    - cron: "0 7-16 * 4-10 *"  # letní čas CEST = 9–18 ČR

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.x"

      - name: Install dependencies
        run: pip install pandas requests tuyapy2

      - name: Run script
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          TUYA_ACCESS_ID: ${{ secrets.TUYA_ACCESS_ID }}
          TUYA_ACCESS_SECRET: ${{ secrets.TUYA_ACCESS_SECRET }}
          TUYA_EMAIL: ${{ secrets.TUYA_EMAIL }}
          TUYA_PASSWORD: ${{ secrets.TUYA_PASSWORD }}
          DEVICE_NAME: ${{ secrets.DEVICE_NAME }}
        run: python ovladani_rele.py
