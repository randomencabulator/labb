import { useEffect, useState } from "react";
import { type SharedChat as Shared, getSharedChat } from "../api";
import { type Turn, turnsReducer } from "../useChat";
import { MessageList } from "./MessageList";

/** A chat someone shared, read-only: /shared/<thread id> */
export function SharedChat({ threadId, onTitle, onOpenChat }: {
  threadId: string;
  onTitle: (title: string) => void;
  onOpenChat: (threadId: string) => void;
}) {
  const [shared, setShared] = useState<Shared | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setShared(null);
    setError(null);
    getSharedChat(threadId).then(
      found => {
        setShared(found);
        onTitle(found.title);
        const replayed = found.events.reduce((all, event) => turnsReducer(all, { type: "event", event }), [] as Turn[]);
        setTurns(turnsReducer(replayed, { type: "replayed" }));
      },
      e => setError((e as Error).message === "No such shared chat" ? "This chat isn't shared, or doesn't exist." : (e as Error).message),
    );
  }, [threadId, onTitle]);

  return (
    <>
      <MessageList
        turns={turns}
        loading={!shared && !error}
        error={error}
        threadId={threadId}
        name={shared?.owner ?? null}
        canChange={false}
        editing={null}
        onEdit={() => {}}
        onSaveEdit={() => {}}
        onRegenerate={() => {}}
        onAsk={() => {}}
      />
      {shared && (
        <div className="read-only">
          <div className="inner">
            <span>
              {shared.mine ? "You shared this chat" : `Shared by ${shared.owner ?? "someone"}`}: anyone on this server with the link can read it.
            </span>
            {shared.mine && (
              <button type="button" onClick={() => onOpenChat(threadId)}>
                Open it to continue
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
