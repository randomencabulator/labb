import { type ReactNode, forwardRef, useEffect, useState } from "react";
import type { Attachment } from "../api";
import type { Turn } from "../useChat";
import { Composer, type ComposerHandle, type ComposerSettings } from "./Composer";
import { type AgentIntro, MessageList } from "./MessageList";

type Props = {
  turns: Turn[];
  loading: boolean;
  error: string | null;
  threadId: string | null;
  streaming: boolean;
  stopping: boolean;
  // Who you are in it (null: a terminal session's "you"), and who it's with when it's empty
  name: string | null;
  agent?: AgentIntro;
  // Messages can be edited and the last reply written again
  canChange: boolean;
  onSend: (message: string, attachments: Attachment[]) => void;
  // Replace message `index` and everything after it
  onReplace: (index: number, message: string) => void;
  onStop: () => void;
  settings: ComposerSettings;
  // Between the messages and the message box: where it runs, a warning
  above?: ReactNode;
  // In place of the message box, for a chat that can't go on here
  readOnly?: ReactNode;
  // The message box can't be used for now
  disabled?: boolean;
  // A different chat: the messages start again (the message box keeps what's typed)
  listKey?: string;
};

/**
 * A conversation and its message box: what the main chat and an app's chat share. Where the turns
 * come from, and what a message is sent with, is the page's; editing a message, writing the last
 * reply again, attachments, voice and the pickers are the same in both.
 */
export const Conversation = forwardRef<ComposerHandle, Props>(function Conversation(
  { turns, loading, error, threadId, streaming, stopping, name, agent, canChange, onSend, onReplace, onStop, settings, above, readOnly, disabled = false, listKey },
  ref,
) {
  // The message being edited, by index
  const [editing, setEditing] = useState<number | null>(null);
  useEffect(() => setEditing(null), [threadId]);

  const replace = (index: number, message: string) => {
    setEditing(null);
    onReplace(index, message);
  };

  const lastTurn = turns.at(-1);
  const lastReply = lastTurn ? lastTurn.parts.flatMap(part => (part.kind === "text" ? [part.text] : [])).join("\n\n") : "";

  return (
    <>
      <MessageList
        key={listKey}
        turns={turns}
        loading={loading}
        error={error}
        threadId={threadId}
        name={name}
        agent={agent}
        onAsk={prompt => onSend(prompt, [])}
        canChange={canChange}
        editing={editing}
        onEdit={setEditing}
        onSaveEdit={replace}
        onRegenerate={() => {
          if (lastTurn) replace(turns.length - 1, lastTurn.user);
        }}
      />
      {readOnly ?? (
        // e.g. while a summary is being written, a message would have to wait for it
        <fieldset className="composer-gate" disabled={disabled}>
          {above}
          <Composer
            ref={ref}
            {...settings}
            streaming={streaming}
            stopping={stopping}
            onSend={onSend}
            onStop={onStop}
            lastReply={lastReply}
            onEditLast={() => {
              if (canChange && turns.length) setEditing(turns.length - 1);
            }}
          />
        </fieldset>
      )}
    </>
  );
});
