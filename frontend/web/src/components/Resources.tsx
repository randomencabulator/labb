import { useCallback, useEffect, useRef, useState } from "react";
import { type ContainerUsage, type SystemUsage, getSystem } from "../api";

// While it's open, read again this often (a read takes about a second: the CPU is sampled)
const EVERY_MS = 5000;

export function bytes(value: number | null | undefined) {
  if (value == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let unit = 0;
  while (n >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit += 1;
  }
  return `${n >= 100 || unit === 0 ? Math.round(n) : n.toFixed(1)} ${units[unit]}`;
}

function duration(seconds: number | null | undefined) {
  if (seconds == null) return "–";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function since(iso: string | null) {
  return iso ? duration((Date.now() - new Date(iso).getTime()) / 1000) : "–";
}

/** When Ollama unloads a model: a time far off means it's kept loaded */
function unloads(until: string | null) {
  if (!until) return "–";
  const seconds = (new Date(until).getTime() - Date.now()) / 1000;
  return seconds > 30 * 86400 ? "kept loaded" : duration(Math.max(0, seconds));
}

/** How full something is, said in words too: the fill's color is never the only sign */
function level(share: number): { name: "ok" | "high" | "full"; label: string } {
  if (share >= 0.9) return { name: "full", label: "nearly full" };
  if (share >= 0.8) return { name: "high", label: "high" };
  return { name: "ok", label: "" };
}

/** A track filled to `used / total`, with the numbers beside it */
function Meter({
  label,
  used,
  total,
  text,
}: {
  label: string;
  used: number;
  total: number;
  text: string;
}) {
  const share = total > 0 ? Math.min(1, used / total) : 0;
  const { name, label: state } = level(share);
  return (
    <div className="meter-row">
      <div className="meter-head">
        <span>{label}</span>
        <span className="meter-value">
          {text}
          {state && <b className={`meter-state ${name}`}> · {state}</b>}
        </span>
      </div>
      <div
        className={`meter ${name}`}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(share * 100)}
        aria-valuetext={`${text}${state ? `, ${state}` : ""}`}
      >
        <span style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

function Tile({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {detail && <div className="stat-detail">{detail}</div>}
    </div>
  );
}

function ServiceRow({ service }: { service: ContainerUsage }) {
  const running = service.status === "running";
  const limited =
    service.memory_limit &&
    service.memory != null &&
    service.memory_limit < 2 ** 50;
  return (
    <tr>
      <td>
        <b>{service.name}</b>
      </td>
      <td className={running ? undefined : "problem-text"}>
        {running ? `up ${since(service.since)}` : service.status}
      </td>
      <td className="num">
        {service.cpu != null ? `${service.cpu.toFixed(1)}%` : "–"}
      </td>
      <td
        className="num"
        title={limited ? `of ${bytes(service.memory_limit)}` : undefined}
      >
        {bytes(service.memory)}
        {limited && (
          <span className="hint"> / {bytes(service.memory_limit)}</span>
        )}
      </td>
      <td className={`num${service.restarts ? " warn-text" : ""}`}>
        {service.restarts || ""}
      </td>
    </tr>
  );
}

/** Settings → Health's resources: the machine, the server, the database, Ollama's models and every container */
export function Resources() {
  const [found, setFound] = useState<SystemUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const busy = useRef(false);

  const read = useCallback(() => {
    // One read at a time, and none while the page is hidden
    if (busy.current || document.hidden) return;
    busy.current = true;
    getSystem()
      .then(
        (next) => {
          setFound(next);
          setError(null);
        },
        (e) => setError((e as Error).message),
      )
      .finally(() => (busy.current = false));
  }, []);

  useEffect(() => {
    read();
    if (!live) return;
    const timer = window.setInterval(read, EVERY_MS);
    return () => window.clearInterval(timer);
  }, [read, live]);

  if (!found)
    return error ? (
      <div className="problem">{error}</div>
    ) : (
      <p className="hint">Measuring…</p>
    );
  const { host, server, database, ollama, containers } = found;
  const cpu = host?.cpu;

  return (
    <section className="resources" aria-label="Resources">
      <div className="resources-head">
        <h3>Resources</h3>
        <label className="hint">
          <input
            type="checkbox"
            checked={live}
            onChange={(e) => setLive(e.target.checked)}
          />{" "}
          Update every 5 seconds
        </label>
      </div>
      {found.problems.map((problem) => (
        <div key={problem} className="warn-note">
          {problem}
        </div>
      ))}
      {host && (
        <div className="meters">
          {cpu != null && (
            <Meter
              label="Processor"
              used={cpu}
              total={100}
              text={`${cpu.toFixed(0)}% of ${host.processors ?? "?"} processors${host.load ? ` · load ${host.load.map((l) => l.toFixed(2)).join(" ")}` : ""}`}
            />
          )}
          {host.memory_total != null && host.memory_used != null && (
            <Meter
              label="Memory"
              used={host.memory_used}
              total={host.memory_total}
              text={`${bytes(host.memory_used)} of ${bytes(host.memory_total)}`}
            />
          )}
          {host.swap_total != null &&
            host.swap_used != null &&
            host.swap_used > 0 && (
              <Meter
                label="Swap"
                used={host.swap_used}
                total={host.swap_total}
                text={`${bytes(host.swap_used)} of ${bytes(host.swap_total)}`}
              />
            )}
          {host.disk_total != null && host.disk_used != null && (
            <Meter
              label="Disk (where the data is)"
              used={host.disk_used}
              total={host.disk_total}
              text={`${bytes(host.disk_used)} of ${bytes(host.disk_total)}`}
            />
          )}
        </div>
      )}
      <div className="stat-tiles">
        {server && (
          <Tile
            label="Server process"
            value={bytes(server.memory ?? server.container_memory)}
            detail={`up ${duration(server.up_seconds)} · ${server.threads} threads · ${server.cpu_seconds.toLocaleString()}s of CPU`}
          />
        )}
        {database && (
          <Tile
            label="Database"
            value={bytes(database.bytes)}
            detail={`${database.kind} · ${database.chats.toLocaleString()} chats`}
          />
        )}
        {host?.up_seconds != null && (
          <Tile label="Machine up" value={duration(host.up_seconds)} />
        )}
        {containers && (
          <Tile
            label="People's apps"
            value={`${containers.apps.running} running`}
            detail={`${bytes(containers.apps.memory)} · ${containers.apps.cpu.toFixed(1)}% CPU`}
          />
        )}
      </div>

      {containers && containers.services.length > 0 && (
        <>
          <h4>Services</h4>
          <div className="table-scroll">
            <table className="settings-table resources-table">
              <thead>
                <tr>
                  <th>Service</th>
                  <th>State</th>
                  <th className="num">CPU</th>
                  <th className="num">Memory</th>
                  <th className="num">Restarts</th>
                </tr>
              </thead>
              <tbody>
                {containers.services.map((service) => (
                  <ServiceRow key={service.container} service={service} />
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            CPU is a share of one processor, so 200% is two of them busy.
          </p>
        </>
      )}
      {containers && containers.apps.top.length > 0 && (
        <>
          <h4>Busiest apps</h4>
          <div className="table-scroll">
            <table className="settings-table resources-table">
              <thead>
                <tr>
                  <th>App</th>
                  <th className="num">Containers</th>
                  <th className="num">CPU</th>
                  <th className="num">Memory</th>
                </tr>
              </thead>
              <tbody>
                {containers.apps.top.map((app) => (
                  <tr key={app.id}>
                    <td>
                      <b>{app.name || app.id}</b>
                    </td>
                    <td className="num">{app.containers}</td>
                    <td className="num">{app.cpu.toFixed(1)}%</td>
                    <td className="num">{bytes(app.memory)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {ollama && (
        <>
          <h4>Models loaded in Ollama</h4>
          {ollama.models.length === 0 ? (
            <p className="hint">
              None right now: Ollama loads a model when it's used, and unloads
              it a while after.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="settings-table resources-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th className="num">Memory</th>
                    <th className="num">On the GPU</th>
                    <th className="num">Unloads in</th>
                  </tr>
                </thead>
                <tbody>
                  {ollama.models.map((model) => (
                    <tr key={model.name}>
                      <td>
                        <b>{model.name}</b>
                      </td>
                      <td className="num">{bytes(model.memory)}</td>
                      <td className="num">
                        {model.memory
                          ? `${Math.round((model.gpu_memory / model.memory) * 100)}%`
                          : "–"}
                      </td>
                      <td className="num">{unloads(model.until)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
