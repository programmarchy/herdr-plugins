# Email Bridge for Herdr

Forward an email to one AgentMail inbox and route it to a live Herdr agent by **workspace/tab**. The bridge uses [AgentMail's polling webhook](https://www.agentmail.to/docs/webhook-polling), which queues incoming events while the bridge is stopped. It uses one inbox for any number of Herdr tabs. AgentMail currently offers polling webhooks only in its US region.

The first version prompts an agent automatically after two checks: the message's `From` address must exactly match your configured address, and AgentMail's DMARC result must be `pass`. Spam, blocked, unauthenticated, and trashed mail are also ignored. Agent replies stay in Herdr. This plugin does not send email or download attachments.

## Address a tab

Put a route anywhere in the subject, including when forwarding:

```text
To: your-inbox@agentmail.to
Subject: Fwd: [herdr:programmarchy/herdr-plugins] Review this proposal
```

The two route parts are the **visible workspace label** and **tab label**, matched without case sensitivity. They must identify exactly one workspace and tab, and that tab must contain exactly one recognized Claude Code or Codex agent. The agent must be idle or done; mail waits in the local queue while it is working, blocked, or unavailable. Duplicate labels are treated as ambiguous. A missing or ambiguous route is ignored; it is never sent to a different agent.

The bridge can also parse `your-inbox+workspace.tab@agentmail.to` if that mail reaches the base inbox and AgentMail preserves the alias in the `To` field. AgentMail's public documentation does **not** confirm plus addressing, so use the subject form until you test it with your own inbox. A subject route takes precedence over a plus route.

## Install and configure

Requires Python 3.9+, Herdr 0.8+, and an [AgentMail](https://www.agentmail.to/) inbox in its US region. Create one AgentMail inbox and a key with access to that inbox's messages and polling webhooks. Do not paste the key into chat or a shell argument.

From a local checkout of this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
herdr plugin link "$PWD"
herdr plugin pane open --plugin programmarchy.email-bridge --entrypoint setup
herdr plugin pane open --plugin programmarchy.email-bridge --entrypoint bridge --placement tab --no-focus
```

The setup pane asks for the inbox, your exact sending address, and the API key. It writes `config.json` with mode `0600` under `herdr plugin config-dir programmarchy.email-bridge`. You can also run `.venv/bin/python bridge.py configure --inbox your-inbox@agentmail.to --sender you@example.com` from a shell; it prompts for the key unless `AGENTMAIL_API_KEY` is already set. The bridge does not start until you open its listening pane. Leave that pane running to receive mail promptly; queued events catch up after a restart. The polling webhook starts collecting mail only after the bridge is run once.

For a GitHub installation, use `herdr plugin install programmarchy/herdr-plugins/herdr-email` instead of the local `link` command. Herdr runs the build steps automatically. Open the same setup pane, then the listening pane. Updates keep the configuration and local queue.

To see the queue without showing your key:

```sh
herdr plugin action invoke programmarchy.email-bridge.status
herdr plugin log list --plugin programmarchy.email-bridge
```

`sent` means Herdr accepted the prompt. `queued` means the target was unavailable or an API call should be retried. `ignored` means it failed sender/authentication/routing checks. `uncertain` means Herdr may have accepted the prompt just before a timeout or crash; the bridge does not resubmit it automatically. Inspect the target agent before manually retrying:

```sh
.venv/bin/python bridge.py retry '<message-id>'
```

## Security and limits

Only the configured exact sender with a passing DMARC result can trigger a prompt. This is stronger than checking the displayed sender name or `From` address alone, but it cannot protect a compromised sending mailbox. Keep AgentMail's API key private and give agents only the permissions you intend them to exercise. The bridge treats quoted/forwarded content as source material in its prompt, but agents should still review requests before sensitive actions.

HTML-only mail is converted to plain text. Bodies are limited to 40,000 characters. Attachments are named in the prompt but are not downloaded. If a message has no text/HTML body, it is ignored.

The queue is stored under `HERDR_PLUGIN_STATE_DIR` and contains message IDs and delivery states, not mail bodies. Herdr's `agent prompt` and the queue database cannot form one atomic transaction; after an interrupted submission, the message is marked `uncertain` for manual review to avoid automatic duplicate prompts.

## Uninstall

Close the Email Bridge pane, then use `herdr plugin unlink programmarchy.email-bridge` for a local checkout or `herdr plugin uninstall programmarchy.email-bridge` for a GitHub installation. Herdr preserves plugin config and state on uninstall. Remove those directories manually if you also want to delete the API key and queue. The AgentMail polling webhook remains in AgentMail until deleted with its API or console; its client ID is `herdr-email-bridge` and it belongs to the configured inbox.
