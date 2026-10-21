"""Offline evaluation of mail extraction and trip answers (stage 2, v6).

Everything here runs on synthetic fixtures. `--mode local` and `--mode replay`
make no external call; `--mode live` goes through the metered ProviderGateway
with an explicit cost cap and is run only by a person.
"""
