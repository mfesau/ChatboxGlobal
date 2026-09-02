"""Da de alta agentes y supervisores desde la línea de órdenes.

Necesario al menos una vez: sin agentes no hay quien inicie sesión en la consola.

Uso::

    python scripts/create_agent.py --email ana@empresa.local --nombre "Ana Rodríguez" \\
        --rol supervisor
    python scripts/create_agent.py --email luis@empresa.local --rol agent --password Secreta123
    python scripts/create_agent.py --listar

Sin ``--password`` el script genera una contraseña temporal y la imprime una
única vez; comuníquela por un canal seguro y pida que se cambie al entrar.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import repositories as repo  # noqa: E402
from app.db.engine import dispose_engine, init_engine, session_scope  # noqa: E402

ROLES = ("agent", "supervisor", "admin")


async def create(
    *,
    email: str,
    display_name: str | None,
    role: str,
    password: str | None,
    tenant: str | None,
    department: str | None,
) -> None:
    settings = get_settings()
    init_engine(settings)
    generated = password is None
    secret = password or secrets.token_urlsafe(9)

    async with session_scope() as session:
        tenant_row = await repo.get_or_create_tenant(
            session, tenant or settings.default_tenant_slug
        )
        if await repo.find_agent_by_email(session, tenant_id=tenant_row.id, email=email):
            print(f"El agente {email} ya existe en el inquilino {tenant_row.slug}.")
            await dispose_engine()
            raise SystemExit(1)

        department_id = None
        if department:
            department_row = await repo.find_department_by_name(
                session, tenant_id=tenant_row.id, name=department
            )
            if department_row is None:
                print(f"No existe el departamento «{department}». Créelo antes desde la consola.")
                await dispose_engine()
                raise SystemExit(1)
            department_id = department_row.id

        agent = await repo.create_agent(
            session,
            tenant_id=tenant_row.id,
            email=email,
            display_name=display_name,
            role=role,
            password_hash=hash_password(secret),
            department_id=department_id,
        )
        await repo.record_audit(
            session,
            tenant_id=tenant_row.id,
            actor="cli",
            action="agent_created",
            subject_type="agent",
            subject_id=str(agent.id),
            detail={"email": agent.email, "role": role},
        )

    await dispose_engine()
    slug = tenant or settings.default_tenant_slug
    print(f"Agente creado: {email} ({role}) en el inquilino {slug}")
    if generated:
        print(f"Contraseña temporal: {secret}")
        print("Anótela ahora: no se volverá a mostrar.")


async def show_list(tenant: str | None) -> None:
    settings = get_settings()
    init_engine(settings)
    async with session_scope() as session:
        tenant_row = await repo.get_or_create_tenant(
            session, tenant or settings.default_tenant_slug
        )
        agents = await repo.list_agents(session, tenant_id=tenant_row.id, only_active=False)

    if not agents:
        print("Todavía no hay agentes. Cree primero un supervisor.")
    else:
        print(f"{'Correo':<32} {'Nombre':<22} {'Rol':<12} {'Estado':<9} Presencia")
        for agent in agents:
            print(
                f"{agent.email:<32} {(agent.display_name or ''):<22} {agent.role:<12} "
                f"{'activo' if agent.is_active else 'inactivo':<9} {agent.presence}"
            )
    await dispose_engine()


async def reset_password(email: str, tenant: str | None) -> None:
    settings = get_settings()
    init_engine(settings)
    secret = secrets.token_urlsafe(9)
    async with session_scope() as session:
        tenant_row = await repo.get_or_create_tenant(
            session, tenant or settings.default_tenant_slug
        )
        agent = await repo.find_agent_by_email(session, tenant_id=tenant_row.id, email=email)
        if agent is None:
            print(f"No existe el agente {email}.")
            await dispose_engine()
            raise SystemExit(1)
        agent.password_hash = hash_password(secret)

    await dispose_engine()
    print(f"Contraseña de {email} restablecida: {secret}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gestión de agentes de la consola")
    parser.add_argument("--email", help="Correo con el que inicia sesión")
    parser.add_argument("--nombre", dest="display_name", help="Nombre visible en la consola")
    parser.add_argument("--rol", dest="role", choices=ROLES, default="agent")
    parser.add_argument("--password", help="Contraseña; si se omite, se genera una")
    parser.add_argument("--inquilino", dest="tenant", help="Slug del inquilino")
    parser.add_argument(
        "--departamento", dest="department", help="Departamento principal (debe existir ya)"
    )
    parser.add_argument("--listar", action="store_true", help="Muestra los agentes existentes")
    parser.add_argument(
        "--restablecer", action="store_true", help="Genera una contraseña nueva para --email"
    )
    args = parser.parse_args()

    if args.listar:
        asyncio.run(show_list(args.tenant))
        return
    if not args.email:
        parser.error("Indique --email, o bien --listar")
    if args.restablecer:
        asyncio.run(reset_password(args.email, args.tenant))
        return

    asyncio.run(
        create(
            email=args.email,
            display_name=args.display_name,
            role=args.role,
            password=args.password,
            tenant=args.tenant,
            department=args.department,
        )
    )


if __name__ == "__main__":
    main()
