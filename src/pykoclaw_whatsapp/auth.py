"""WhatsApp QR authentication flow."""

from __future__ import annotations

import asyncio

import click
from neonize.client import NewClient
from neonize.events import ConnectedEv, QRCodeEv

from .config import get_config


async def run_auth() -> None:
    """Authenticate with WhatsApp using QR code."""
    config = get_config()
    config.auth_dir.mkdir(parents=True, exist_ok=True)

    click.echo("Starting WhatsApp authentication...\n")

    client = NewClient(
        "pykoclaw-whatsapp",
        database=str(config.session_db),
    )

    qr_displayed = False

    @client.event
    def on_qr(client: NewClient, event: QRCodeEv) -> None:
        nonlocal qr_displayed
        if not qr_displayed:
            click.echo("Scan this QR code with WhatsApp:\n")
            click.echo("  1. Open WhatsApp on your phone")
            click.echo("  2. Tap Settings → Linked Devices → Link a Device")
            click.echo("  3. Point your camera at the QR code below\n")
            qr_displayed = True

        try:
            import qrcode

            qr = qrcode.QRCode()
            qr.add_data(event.code)
            qr.make()
            qr.print_ascii()
        except ImportError:
            click.echo(f"QR Code: {event.code}\n")

    @client.event
    def on_connected(client: NewClient, event: ConnectedEv) -> None:
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
