import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Modal, ModalSize, useResponsive } from "@gouvfr-lasuite/ui-components";
import { errorToString } from "@/features/api/APIError";
import { Subject } from "../data/types";

/**
 * The first time someone opens the graph.
 *
 * Two things are missing on a first visit, and neither is obvious from the
 * drawing itself: what the marks mean, and what the drive is about. The first
 * is three sentences. The second cannot be guessed ‒ a drive only sorts itself
 * once somebody has said what they work on ‒ so the screen asks, and turns the
 * answer into subjects the files can fall into straight away.
 *
 * It is shown once, and only to a drive that holds no subject yet: someone who
 * has already written one has answered the question.
 */

/** Subjects a team writes for itself, grouped by what the team does. */
const ROLE_PRESETS: Record<string, string[]> = {
  hr: ["recruitment", "contracts", "leave", "training"],
  legal: ["labour_law", "procurement", "privacy", "disputes"],
  finance: ["budget", "grants", "invoices", "procurement"],
  comms: ["publications", "events", "press", "web"],
  tech: ["infrastructure", "security", "development", "documentation"],
  lead: ["strategy", "committees", "budget", "recruitment"],
};
const ROLES = Object.keys(ROLE_PRESETS);

export const WELCOME_STORAGE_KEY = "drive-graph-welcomed";

type GraphWelcomeProps = {
  onClose: () => void;
  existingSubjects: Pick<Subject, "name">[];
  onCreate: (subject: { name: string; description: string }) => Promise<void>;
  onComplete: (count: number) => void;
};

export const GraphWelcome = ({ onClose, existingSubjects, onCreate, onComplete }: GraphWelcomeProps) => {
  const { t } = useTranslation();
  const { isDesktop } = useResponsive();
  const [role, setRole] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [created, setCreated] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const nameKey = (name: string) => name.trim().toLocaleLowerCase();
  const existing = new Set([...existingSubjects.map(({ name }) => nameKey(name)), ...created]);
  const alreadyExists = (key: string) => existing.has(nameKey(t(`graph.presets.${key}.name`)));
  const pending = selected.filter((key) => !alreadyExists(key));

  const pick = (role: string) => {
    setRole(role);
    setSelected(ROLE_PRESETS[role].filter((key) => !alreadyExists(key)));
    setError("");
  };

  const create = async () => {
    if (busy || !pending.length) return;
    setBusy(true);
    setError("");
    try {
      // Keep successful creations even if a later request fails. Retrying
      // submits only the remaining choices, including across role changes.
      for (const key of pending) {
        const name = t(`graph.presets.${key}.name`);
        await onCreate({ name, description: t(`graph.presets.${key}.description`) });
        setCreated((previous) => [...previous, nameKey(name)]);
      }
      onComplete(pending.length);
      onClose();
    } catch (cause) {
      setError(errorToString(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      isOpen
      closeOnClickOutside={!busy}
      onClose={() => { if (!busy) onClose(); }}
      size={isDesktop ? ModalSize.MEDIUM : ModalSize.FULL}
      title={t("graph.welcome_title")}
      aria-label={t("graph.welcome_title")}
      rightActions={
        <>
          <Button variant="tertiary" onClick={onClose} disabled={busy}>
            {t("graph.close")}
          </Button>
          {role && (
            <Button onClick={() => void create()} disabled={busy || !pending.length}>
              {t(busy ? "graph.welcome_creating" : "graph.welcome_create", { count: pending.length })}
            </Button>
          )}
        </>
      }
    >
      <div className="file-graph__welcome">
        {!role && <>
        <ul className="file-graph__welcome-how">
          <li>
            <span className="file-graph__welcome-mark" aria-hidden="true">
              <svg width="15" height="15" viewBox="0 0 15 15">
                <circle cx="7.5" cy="7.5" r="5.5" />
              </svg>
              <svg width="15" height="15" viewBox="0 0 15 15">
                <rect x="2" y="2" width="11" height="11" rx="2.4" />
              </svg>
              <svg width="15" height="15" viewBox="0 0 15 15">
                <polygon points="7.5,1.5 13.5,12.6 1.5,12.6" />
              </svg>
            </span>
            {t("graph.welcome_shapes")}
          </li>
          <li>
            <span className="file-graph__welcome-mark" aria-hidden="true">
              <svg width="15" height="15" viewBox="0 0 15 15">
                <circle cx="7.5" cy="7.5" r="5.5" fill="#B0005C" />
              </svg>
              <svg width="15" height="15" viewBox="0 0 15 15">
                <circle cx="7.5" cy="7.5" r="5.5" fill="#009ED9" />
              </svg>
              <svg width="15" height="15" viewBox="0 0 15 15">
                <circle cx="7.5" cy="7.5" r="5.5" fill="#65A800" />
              </svg>
            </span>
            {t("graph.welcome_colors")}
          </li>
          <li>
            <span className="file-graph__welcome-mark" aria-hidden="true">
              <svg width="15" height="15" viewBox="0 0 15 15">
                <circle cx="4" cy="7.5" r="2.6" />
                <circle cx="11" cy="7.5" r="2.6" />
                <line x1="6.6" y1="7.5" x2="8.4" y2="7.5" strokeWidth="1.4" />
              </svg>
            </span>
            {t("graph.welcome_links")}
          </li>
        </ul>
        <p className="file-graph__welcome-navigation">{t("graph.welcome_navigation")}</p>

        <p className="file-graph__welcome-ask">{t("graph.welcome_ask")}</p>
        <div className="file-graph__welcome-roles">
          {ROLES.map((role) => (
            <button
              key={role}
              type="button"
              className="file-graph__welcome-role"
              disabled={busy}
              onClick={() => pick(role)}
            >
              <b>{t(`graph.roles.${role}`)}</b>
              <span>
                {ROLE_PRESETS[role].map((key) => t(`graph.presets.${key}.name`)).join(" · ")}
              </span>
            </button>
          ))}
        </div>
        </>}
        {role && <>
          <Button variant="tertiary" size="small" disabled={busy} onClick={() => { setRole(null); setError(""); }}>
            {t("graph.welcome_back")}
          </Button>
          <h3 className="file-graph__welcome-preview-title">{t(`graph.roles.${role}`)}</h3>
          <p className="file-graph__welcome-preview-hint">{t("graph.welcome_choose")}</p>
          <div className="file-graph__welcome-choices">
            {ROLE_PRESETS[role].map((key) => {
              const exists = alreadyExists(key);
              return (
                <label className={`file-graph__welcome-choice${exists ? " file-graph__welcome-choice--existing" : ""}`} key={key}>
                  <input
                    type="checkbox"
                    checked={exists || selected.includes(key)}
                    disabled={busy || exists}
                    onChange={(event) => setSelected((previous) => event.target.checked ? [...previous, key] : previous.filter((item) => item !== key))}
                  />
                  <span>
                    <b>{t(`graph.presets.${key}.name`)}</b>
                    <span>{exists ? t("graph.welcome_existing") : t(`graph.presets.${key}.description`)}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </>}
        {error && <p className="file-graph__form-error" role="alert">{error}</p>}
      </div>
    </Modal>
  );
};
