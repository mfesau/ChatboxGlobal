-- Esquema inicial del orquestador omnicanal.
-- Generado con: python scripts/generate_ddl.py
-- No editar a mano: modifique app/db/models.py y vuelva a generar.

BEGIN;


-- inbound_dedupe
CREATE TABLE inbound_dedupe (
	dedupe_key VARCHAR(255) NOT NULL, 
	channel VARCHAR(32) NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (dedupe_key)
);
CREATE INDEX ix_inbound_dedupe_received_at ON inbound_dedupe (received_at);

-- tenants
CREATE TABLE tenants (
	id UUID NOT NULL, 
	slug VARCHAR(64) NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	settings JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (slug)
);
CREATE INDEX ix_tenants_created_at ON tenants (created_at);

-- audit_log
CREATE TABLE audit_log (
	id UUID NOT NULL, 
	tenant_id UUID, 
	actor VARCHAR(160) NOT NULL, 
	action VARCHAR(64) NOT NULL, 
	subject_type VARCHAR(64), 
	subject_id VARCHAR(64), 
	detail JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);
CREATE INDEX ix_audit_tenant_time ON audit_log (tenant_id, created_at);

-- channel_accounts
CREATE TABLE channel_accounts (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	channel VARCHAR(32) NOT NULL, 
	external_id VARCHAR(128) NOT NULL, 
	display_name VARCHAR(160), 
	is_active BOOLEAN NOT NULL, 
	config JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_channel_account_external UNIQUE (channel, external_id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);
CREATE INDEX ix_channel_accounts_created_at ON channel_accounts (created_at);
CREATE INDEX ix_channel_accounts_tenant ON channel_accounts (tenant_id, channel);

-- contacts
CREATE TABLE contacts (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	display_name VARCHAR(160), 
	primary_phone VARCHAR(32), 
	primary_email VARCHAR(254), 
	locale VARCHAR(16), 
	is_blocked BOOLEAN NOT NULL, 
	attributes JSONB NOT NULL, 
	password_hash VARCHAR(255), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_contact_email UNIQUE (tenant_id, primary_email), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);
CREATE INDEX ix_contacts_created_at ON contacts (created_at);
CREATE INDEX ix_contacts_tenant_phone ON contacts (tenant_id, primary_phone);

-- departments
CREATE TABLE departments (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_department_name UNIQUE (tenant_id, name), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);
CREATE INDEX ix_departments_created_at ON departments (created_at);

-- agents
CREATE TABLE agents (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	display_name VARCHAR(160), 
	role VARCHAR(32) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	password_hash VARCHAR(255), 
	presence VARCHAR(16) NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE, 
	department_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_agent_email UNIQUE (tenant_id, email), 
	CONSTRAINT ck_agent_role CHECK (role IN ('agent','supervisor','admin')), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE SET NULL
);
CREATE INDEX ix_agents_created_at ON agents (created_at);
CREATE INDEX ix_agents_tenant_role ON agents (tenant_id, role);

-- contact_identities
CREATE TABLE contact_identities (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	contact_id UUID NOT NULL, 
	channel VARCHAR(32) NOT NULL, 
	channel_user_id VARCHAR(255) NOT NULL, 
	display_name VARCHAR(160), 
	raw JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_identity_channel_user UNIQUE (tenant_id, channel, channel_user_id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE
);
CREATE INDEX ix_contact_identities_contact_id ON contact_identities (contact_id);
CREATE INDEX ix_contact_identities_created_at ON contact_identities (created_at);

-- contact_sessions
CREATE TABLE contact_sessions (
	token_hash VARCHAR(64) NOT NULL, 
	contact_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	client_ip VARCHAR(64), 
	user_agent VARCHAR(255), 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE
);
CREATE INDEX ix_contact_sessions_contact ON contact_sessions (contact_id, expires_at);

-- agent_departments
CREATE TABLE agent_departments (
	agent_id UUID NOT NULL, 
	department_id UUID NOT NULL, 
	PRIMARY KEY (agent_id, department_id), 
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE, 
	FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE CASCADE
);

-- agent_sessions
CREATE TABLE agent_sessions (
	token_hash VARCHAR(64) NOT NULL, 
	agent_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	client_ip VARCHAR(64), 
	user_agent VARCHAR(255), 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);
CREATE INDEX ix_agent_sessions_agent ON agent_sessions (agent_id, expires_at);

-- contact_comments
CREATE TABLE contact_comments (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	contact_id UUID NOT NULL, 
	agent_id UUID, 
	body TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE, 
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE SET NULL
);
CREATE INDEX ix_contact_comments_contact ON contact_comments (contact_id, created_at);

-- conversations
CREATE TABLE conversations (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	channel VARCHAR(32) NOT NULL, 
	channel_conversation_id VARCHAR(255) NOT NULL, 
	channel_account_id UUID, 
	contact_id UUID, 
	assignee_id UUID, 
	department_id UUID, 
	status VARCHAR(16) NOT NULL, 
	control VARCHAR(16) NOT NULL, 
	subject VARCHAR(255), 
	last_message_at TIMESTAMP WITH TIME ZONE, 
	unread_count INTEGER NOT NULL, 
	conversation_ref JSONB NOT NULL, 
	state JSONB NOT NULL, 
	tags JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_conversation_channel_thread UNIQUE (tenant_id, channel, channel_conversation_id), 
	CONSTRAINT ck_conversation_status CHECK (status IN ('open','snoozed','closed')), 
	CONSTRAINT ck_conversation_control CHECK (control IN ('bot','human')), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(channel_account_id) REFERENCES channel_accounts (id) ON DELETE SET NULL, 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL, 
	FOREIGN KEY(assignee_id) REFERENCES agents (id) ON DELETE SET NULL, 
	FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE SET NULL
);
CREATE INDEX ix_conversations_contact_id ON conversations (contact_id);
CREATE INDEX ix_conversations_created_at ON conversations (created_at);
CREATE INDEX ix_conversations_department_id ON conversations (department_id);
CREATE INDEX ix_conversations_inbox ON conversations (tenant_id, status, last_message_at);

-- assignments
CREATE TABLE assignments (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	action VARCHAR(16) NOT NULL, 
	from_agent_id UUID, 
	to_agent_id UUID, 
	by_agent_id UUID, 
	to_department_id UUID, 
	note TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_assignment_action CHECK (action IN ('claim','transfer','release','close','reopen','transfer_department')), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
	FOREIGN KEY(from_agent_id) REFERENCES agents (id) ON DELETE SET NULL, 
	FOREIGN KEY(to_agent_id) REFERENCES agents (id) ON DELETE SET NULL, 
	FOREIGN KEY(by_agent_id) REFERENCES agents (id) ON DELETE SET NULL, 
	FOREIGN KEY(to_department_id) REFERENCES departments (id) ON DELETE SET NULL
);
CREATE INDEX ix_assignments_conversation ON assignments (conversation_id, created_at);
CREATE INDEX ix_assignments_to_agent ON assignments (to_agent_id, created_at);

-- internal_notes
CREATE TABLE internal_notes (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	agent_id UUID, 
	body TEXT NOT NULL, 
	mentions JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE SET NULL
);
CREATE INDEX ix_internal_notes_conversation ON internal_notes (conversation_id, created_at);

-- messages
CREATE TABLE messages (
	id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	channel VARCHAR(32) NOT NULL, 
	direction VARCHAR(32) NOT NULL, 
	content_type VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	provider_message_id VARCHAR(255), 
	client_message_id UUID, 
	text TEXT, 
	attachments JSONB NOT NULL, 
	action JSONB, 
	author_type VARCHAR(16) NOT NULL, 
	author_contact_id UUID, 
	author_agent_id UUID, 
	sent_at TIMESTAMP WITH TIME ZONE, 
	raw JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_message_provider_id UNIQUE (channel, provider_message_id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(author_contact_id) REFERENCES contacts (id) ON DELETE SET NULL, 
	FOREIGN KEY(author_agent_id) REFERENCES agents (id) ON DELETE SET NULL
);
CREATE INDEX ix_messages_client_id ON messages (client_message_id);
CREATE INDEX ix_messages_created_at ON messages (created_at);
CREATE INDEX ix_messages_thread ON messages (conversation_id, created_at);

-- ai_runs
CREATE TABLE ai_runs (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	message_id UUID, 
	model VARCHAR(64) NOT NULL, 
	handler VARCHAR(64) NOT NULL, 
	input_tokens INTEGER, 
	output_tokens INTEGER, 
	cache_read_tokens INTEGER, 
	latency_ms INTEGER, 
	stop_reason VARCHAR(32), 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
	FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE SET NULL
);
CREATE INDEX ix_ai_runs_conversation ON ai_runs (conversation_id, created_at);

-- message_events
CREATE TABLE message_events (
	id UUID NOT NULL, 
	message_id UUID NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	provider_status VARCHAR(64), 
	error_code VARCHAR(64), 
	error_detail TEXT, 
	occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	payload JSONB NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE CASCADE
);
CREATE INDEX ix_message_events_message ON message_events (message_id, occurred_at);

-- outbox
CREATE TABLE outbox (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	message_id UUID, 
	channel VARCHAR(32) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	payload JSONB NOT NULL, 
	last_error TEXT, 
	locked_by VARCHAR(64), 
	locked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_outbox_status CHECK (status IN ('pending','in_progress','sent','failed','dead')), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
	FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE SET NULL
);
CREATE INDEX ix_outbox_created_at ON outbox (created_at);
CREATE INDEX ix_outbox_ready ON outbox (status, next_attempt_at);

COMMIT;
