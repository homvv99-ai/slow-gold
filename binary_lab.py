name: مختبر

on:
  workflow_dispatch:
    inputs:
      action:
        description: 'action: probe|backtest|explore|payouts|buyschema|live'
        required: false
        default: 'probe'
  repository_dispatch:
    types: [pulse]
  schedule:
    - cron: '*/5 * * * *'

permissions:
  contents: write

concurrency:
  group: lab
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 360
    env:
      DERIV_TOKEN: ${{ secrets.DERIV_TOKEN }}
      TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
      TELEGRAM_CHAT: ${{ secrets.TELEGRAM_CHAT }}
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: pip
        run: pip install websocket-client requests
      - name: run lab
        run: |
          git config --global user.name "slow-gold lab"
          git config --global user.email "lab@slowgold.local"
          ACTION="${{ github.event.inputs.action }}"
          if [ "${{ github.event_name }}" = "schedule" ]; then ACTION="live"; fi
          if [ "${{ github.event_name }}" = "repository_dispatch" ]; then ACTION="live"; fi
          if [ -z "$ACTION" ]; then ACTION="probe"; fi
          echo "ACTION=$ACTION"
          python binary_lab.py "$ACTION"
      - name: commit results
        run: |
          git add -f site/data 2>/dev/null || true
          if ! git diff --cached --quiet; then
            git commit -m "lab: results"
            git push origin HEAD:${{ github.ref_name }}
          fi
