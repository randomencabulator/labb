import { useEffect, useMemo, useState } from "react";
import {
  type ArtifactDetail,
  type ArtifactInfo,
  deleteArtifact,
  deleteArtifactFile,
  getArtifact,
  listArtifacts,
  publishArtifact,
  readArtifactFile,
  searchArtifactFiles,
  writeArtifactFile,
} from "../api";
import { relativeTime } from "../format";
import { Link, chatPath, pathOf } from "../router";
import { ArtifactCard } from "./ArtifactCard";
import { FileEditor, type FileStore } from "./FileEditor";

function state(artifact: ArtifactInfo) {
  if (artifact.build?.ok === false)
    return artifact.url
      ? "last build failed (the one before is up)"
      : "its build failed";
  return artifact.url ? "published" : "not published yet";
}

/** The Artifacts page: the web pages the model built in your chats */
export function ArtifactsTab({
  opened,
  onOpen,
  onOpenChat,
}: {
  opened: string | null;
  onOpen: (name: string | null) => void;
  onOpenChat: (threadId: string) => void;
}) {
  if (opened)
    return (
      <ArtifactView
        name={opened}
        onBack={() => onOpen(null)}
        onOpenChat={onOpenChat}
      />
    );
  return <ArtifactList />;
}

function ArtifactList() {
  const [found, setFound] = useState<{
    available: boolean;
    artifacts: ArtifactInfo[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listArtifacts().then(setFound, (e) => setError((e as Error).message));
  }, []);

  if (error) return <div className="problem">{error}</div>;
  if (!found) return <p className="hint">Loading…</p>;
  if (!found.available)
    return (
      <p className="hint">
        This server can't build web pages: it needs the spawner and labb-sites
        (docker compose runs them).
      </p>
    );
  return (
    <>
      <p className="hint">
        Web pages the assistant built in your chats: dashboards, reports,
        diagrams. Anyone with a page's link can open it. Deleting a chat deletes
        its pages too.
      </p>
      {found.artifacts.length === 0 && (
        <p className="hint">
          None yet. Ask for one in a chat, e.g. “make a page that charts these
          numbers”.
        </p>
      )}
      <div className="app-tiles">
        {found.artifacts.map((artifact) => (
          <Link
            key={artifact.name}
            to={pathOf("artifacts", artifact.name)}
            className="app-tile"
          >
            <b>{artifact.title}</b>
            <span
              className={`muted${artifact.build?.ok === false ? " warn-text" : ""}`}
            >
              {state(artifact)}
            </span>
            {artifact.chat && (
              <span className="muted app-tile-detail">
                From “{artifact.chat}”
              </span>
            )}
            {artifact.workflow && (
              <span className="muted app-tile-detail">
                Made by the workflow “{artifact.workflow}”
              </span>
            )}
            {artifact.created && (
              <span className="muted">
                Made {relativeTime(artifact.created)}
              </span>
            )}
          </Link>
        ))}
      </div>
    </>
  );
}

function ArtifactView({
  name,
  onBack,
  onOpenChat,
}: {
  name: string;
  onBack: () => void;
  onOpenChat: (threadId: string) => void;
}) {
  const [artifact, setArtifact] = useState<ArtifactDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  // Files changed here since it was last published, and a publish under way
  const [unpublished, setUnpublished] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);

  useEffect(() => {
    getArtifact(name).then(setArtifact, (e) => setError((e as Error).message));
  }, [name]);

  // Its files, in the editor apps use; the listing leaves out the template's examples
  const store = useMemo<FileStore>(
    () => ({
      list: () => getArtifact(name).then((found) => found.files),
      read: (path) => readArtifactFile(name, path).then((read) => read.content),
      write: (path, content) => writeArtifactFile(name, path, content),
      remove: (path) => deleteArtifactFile(name, path),
      search: (query, options, signal) =>
        searchArtifactFiles(name, query, options, signal),
    }),
    [name],
  );

  const publish = async () => {
    setPublishing(true);
    setPublishError(null);
    try {
      const done = await publishArtifact(name);
      if (done.ok) setUnpublished(false);
      else setPublishError(done.message);
      setArtifact(await getArtifact(name));
    } catch (e) {
      setPublishError((e as Error).message);
    } finally {
      setPublishing(false);
    }
  };

  const remove = async () => {
    if (
      !artifact ||
      !confirm(
        `Delete “${artifact.title}”? Its files and its page go, and its link stops working.`,
      )
    )
      return;
    setDeleting(true);
    try {
      await deleteArtifact(name);
      onBack();
    } catch (e) {
      setError((e as Error).message);
      setDeleting(false);
    }
  };

  if (error && !artifact) return <div className="problem">{error}</div>;
  if (!artifact) return <p className="hint">Loading…</p>;
  return (
    <div className="artifact-view">
      <div className="artifact-view-head">
        <button type="button" className="link" onClick={onBack}>
          ← All artifacts
        </button>
        <h3>{artifact.title}</h3>
        <span
          className={`muted${artifact.build?.ok === false ? " warn-text" : ""}`}
        >
          {state(artifact)}
        </span>
        <div className="artifact-view-actions">
          {artifact.thread && artifact.chat && (
            <a
              href={chatPath(artifact.thread)}
              onClick={(event) => {
                event.preventDefault();
                onOpenChat(artifact.thread!);
              }}
            >
              Open the chat
            </a>
          )}
          <button
            type="button"
            className={unpublished ? "primary" : undefined}
            onClick={publish}
            disabled={publishing}
            title="Build the page from its files and publish it (10 to 30 seconds)"
          >
            {publishing ? "Publishing…" : unpublished ? "Publish changes" : "Publish"}
          </button>
          <button
            type="button"
            className="danger"
            onClick={remove}
            disabled={deleting}
          >
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
      {error && <div className="problem">{error}</div>}
      <p className="hint">
        Made {artifact.created ? relativeTime(artifact.created) : "a while ago"}
        {artifact.workflow
          ? ` by the workflow “${artifact.workflow}”`
          : artifact.chat
            ? ` in “${artifact.chat}”`
            : artifact.thread
              ? " in a chat that's gone"
              : ""}
        {artifact.build?.at
          ? ` · last built ${relativeTime(artifact.build.at)}`
          : ""}
        {artifact.url && (
          <>
            {" · "}
            <code>{artifact.url}</code>
          </>
        )}
      </p>
      {publishError ? (
        <pre className="artifact-build-error">{publishError}</pre>
      ) : (
        artifact.build?.ok === false &&
        artifact.build.error && (
          <pre className="artifact-build-error">{artifact.build.error}</pre>
        )
      )}
      {artifact.url ? (
        // A new build: a fresh frame, so it shows
        <ArtifactCard
          key={artifact.build?.at ?? ""}
          url={artifact.url}
          name={artifact.name}
        />
      ) : (
        <p className="hint">
          It hasn't been published: Publish builds it, or ask for it in its
          chat.
        </p>
      )}

      <h4>Files</h4>
      <div className="artifact-editor">
        <FileEditor
          store={store}
          what="the page's"
          hint={
            <>
              Pick a file to read or change it, then Publish to see the change.
              {artifact.examples > 0 &&
                ` The template's ${artifact.examples} example files are left out.`}
            </>
          }
          onChanged={() => setUnpublished(true)}
        />
      </div>
    </div>
  );
}
