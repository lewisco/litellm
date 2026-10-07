# n8n agents through LiteLLM

Use LiteLLM's agent gateway to invoke an n8n agent through a Chat Trigger production webhook. LiteLLM exposes A2A requests, applies gateway authentication and agent access controls, and translates text messages and streaming replies to n8n's chat protocol

## Prepare the n8n workflow

Create a workflow with a [Chat Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.chattrigger/) in **Embedded Chat** mode. Enable **Make Chat Publicly Available**, activate or publish the workflow, and copy its production **Chat URL**. Use the full URL shown by n8n rather than the editor's test webhook URL

For a published [n8n Agent](https://docs.n8n.io/build/build-and-manage-agents.md), connect [Message an Agent](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.messageanagent), select the published agent, and use `{{ $json.chatInput }}` as the message. Set its session option to **Connected Chat Trigger Node** so the agent reuses the incoming session

For an existing [AI Agent workflow](https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent/), connect the AI Agent node, its chat model, and its tools. Use the incoming `chatInput` as the prompt. Configure its memory to use the incoming `sessionId` for conversation history

Set the Chat Trigger's **Response Mode** to **Streaming response** and enable streaming in the agent node for streaming clients. Use a text reply rather than the Message an Agent node's structured JSON reply mode, which disables streaming. For non-streaming workflows, use **When Last Node Finishes** and return the assistant reply in an `output` or `text` field

## Register the agent

Add this agent to your LiteLLM proxy configuration, replacing the URL with your production Chat URL

```yaml
agents:
  - agent_name: n8n-assistant
    agent_card_params:
      protocolVersion: "0.3"
      name: "n8n Assistant"
      description: "Assistant hosted in n8n"
      url: "https://your-n8n.example.com/webhook/your-workflow-id/chat"
      version: "1.0.0"
      defaultInputModes: ["text"]
      defaultOutputModes: ["text"]
      capabilities:
        streaming: true
      skills:
        - id: chat
          name: Chat
          description: "Answer questions using the n8n agent"
          tags: ["chat"]
    litellm_params:
      custom_llm_provider: n8n
      model: n8n/agent
```

Set `capabilities.streaming` to `false` if your workflow does not stream. The example pins A2A protocol version `0.3` for the JSON-RPC requests below; LiteLLM also supports version `1.0` clients

Alternatively, open **Agents** in the LiteLLM Admin UI, choose **n8n**, and enter the production Chat URL. The UI expects a workflow with streaming enabled. Use the agent configuration or registration API to set authentication headers

## Protect the upstream endpoint

The Chat Trigger supports **Basic Auth**. Configure that credential in n8n, then add the matching authorization header at the agent's top level in your LiteLLM configuration

```yaml
    static_headers:
      Authorization: "Basic <base64-encoded-username:password>"
```

For an endpoint protected by a reverse proxy with Header authentication, configure its expected header instead

```yaml
    static_headers:
      X-Agent-Token: "<upstream-token>"
```

An optional `api_key` in `litellm_params`, or the Admin UI's **Bearer Token** field, sends `Authorization: Bearer <token>`. Use this only when the upstream endpoint accepts bearer authentication. An n8n REST API key does not automatically authenticate a Chat Trigger. Static headers take precedence over the bearer token

Keep the upstream credentials on the gateway. Clients use their LiteLLM virtual keys and must have access to this registered agent

## Send a message

With the proxy running on port 4000, invoke the configured agent by name. Replace `$LITELLM_API_KEY` with an authorized virtual key or use the proxy's master key for an initial check

```bash
curl http://localhost:4000/a2a/n8n-assistant/message/send \
  -H "Authorization: Bearer $LITELLM_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{
    "jsonrpc": "2.0",
    "id": "request-1",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "message-1",
        "contextId": "conversation-1",
        "role": "user",
        "parts": [{"kind": "text", "text": "What can you help me with?"}]
      }
    }
  }'
```

For a streaming reply, send `message/stream` to the same endpoint and disable curl's response buffering

```bash
curl -N http://localhost:4000/a2a/n8n-assistant/message/send \
  -H "Authorization: Bearer $LITELLM_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{
    "jsonrpc": "2.0",
    "id": "request-2",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "message-2",
        "contextId": "conversation-1",
        "role": "user",
        "parts": [{"kind": "text", "text": "Tell me more"}]
      }
    }
  }'
```

## Sessions and accounting

LiteLLM converts text parts into `chatInput` and uses the A2A `contextId` for n8n's `sessionId`. Through the proxy, it binds the session to the authenticated key so two different keys using the same `contextId` do not share chat memory. Reuse the same key and `contextId` to continue a conversation. Session persistence still depends on the n8n workflow's memory or session-reuse configuration

This adapter accepts text messages. It does not create or publish n8n agents, expose approval actions, or implement task cancellation and resubscription

The outer agent call does not report exact token usage or the costs of models, tools, and subagents inside n8n. Route n8n's underlying model calls through LiteLLM separately when you need their actual model usage and spend
