"""WhatsApp QR authentication flow."""

from __future__ import annotations

import asyncio

import click
import qrcode
from neonize.client import NewClient
from neonize.events import ConnectedEv

from .config import get_config


async def run_auth() -> None:
    """Authenticate with WhatsApp using QR code."""
    config = get_config()
    config.auth_dir.mkdir(parents=True, exist_ok=True)

    click.echo("Starting WhatsApp authentication...\n")

    client = NewClient(str(config.session_db))

    qr_displayed = False

    @client.qr
    def on_qr(_client: NewClient, data_qr: bytes) -> None:
        nonlocal qr_displayed
        if not qr_displayed:
            click.echo("Scan this QR code with WhatsApp:\n")
            click.echo("  1. Open WhatsApp on your phone")
            click.echo("  2. Tap Settings → Linked Devices → Link a Device")
            click.echo("  3. Point your camera at the QR code below\n")
            qr_displayed = True

        qr = qrcode.QRCode()
        qr.add_data(data_qr)
        qr.make()
        qr.print_ascii()

    @client.event(ConnectedEv)
    def on_connected(_client: NewClient, event: ConnectedEv) -> None:
        click.echo("\n✓ Successfully authenticated with WhatsApp!")
        click.echo(f"  Credentials saved to {config.auth_dir}/")
        click.echo("  You can now start the pykoclaw WhatsApp service.\n")
        asyncio.create_task(shutdown_client(client))

    async def shutdown_client(client: NewClient) -> None:
        await asyncio.sleep(1)
        client.disconnect()

    try:
        client.connect()
    except KeyboardInterrupt:
        click.echo("\n✗ Authentication cancelled.")
        raise SystemExit(1)
    except Exception as err:
        click.echo(f"\n✗ Authentication failed: {err}")
        raise SystemExit(1)
