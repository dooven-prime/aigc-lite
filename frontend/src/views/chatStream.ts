/** Parse the authenticated POST SSE response without assuming network chunk boundaries. */
export async function consumeChatStream(
  response: Response,
  onDelta: (content: string) => void,
): Promise<void> {
  if (!response.body) throw new Error("Streaming response has no body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;

  const consumeEvent = (event: string) => {
    const data = event.split("\n").filter(line => line.startsWith("data:"))
      .map(line => line.slice(5).trimStart()).join("\n");
    if (!data) return;
    if (data === "[DONE]") { finished = true; return; }
    const value = JSON.parse(data) as { content?: unknown; error?: unknown };
    if (typeof value.error === "string") throw new Error(value.error);
    if (typeof value.content === "string") onDelta(value.content);
  };

  try {
    while (!finished) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        consumeEvent(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (finished) break;
        boundary = buffer.indexOf("\n\n");
      }
    }
    if (!finished) throw new Error("Stream ended before completion; check the Run before retrying");
  } finally {
    reader.releaseLock();
  }
}
