import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Button,
  Input,
  Modal,
  ModalSize,
  TextArea,
  useResponsive,
} from "@gouvfr-lasuite/ui-components";
import { Subject } from "../data/types";
import { errorToString } from "@/features/api/APIError";

type SubjectModalProps = {
  /** The subject being edited, or null when a new one is being named. */
  subject: Subject | null;
  onClose: () => void;
  onSave: (draft: { name: string; description: string }) => Promise<void>;
  /** Absent while creating: there is nothing to drop yet. */
  onDelete?: () => Promise<void>;
};

/**
 * Naming a subject, describing it, or dropping it.
 *
 * The description is the part that does the work: the backend reads it to
 * decide which files belong, so the field says so rather than sitting there
 * unlabelled.
 */
export const SubjectModal = ({ subject, onClose, onSave, onDelete }: SubjectModalProps) => {
  const { t } = useTranslation();
  const { isDesktop } = useResponsive();
  const [name, setName] = useState(subject?.name ?? "");
  const [description, setDescription] = useState(subject?.description ?? "");
  const trimmed = name.trim();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
      onClose();
    } catch (cause) {
      setError(errorToString(cause));
    } finally {
      setBusy(false);
    }
  };
  const save = () => trimmed && run(() => onSave({ name: trimmed, description: description.trim() }));

  return (
    <Modal
      isOpen
      closeOnClickOutside={!busy}
      onClose={() => { if (!busy) onClose(); }}
      size={isDesktop ? ModalSize.SMALL : ModalSize.FULL}
      title={subject ? t("graph.subject_edit", { name: subject.name }) : t("graph.subject_new")}
      aria-label={subject ? t("graph.subject_edit", { name: subject.name }) : t("graph.subject_new")}
      leftActions={
        onDelete && (
          <Button
            variant="tertiary"
            color="error"
            disabled={busy}
            onClick={() => void run(onDelete)}
          >
            {t("graph.subject_delete")}
          </Button>
        )
      }
      rightActions={
        <>
          <Button variant="tertiary" onClick={onClose} disabled={busy}>
            {t("graph.close")}
          </Button>
          <Button disabled={!trimmed || busy} onClick={() => void save()}>
            {t(busy ? "graph.subject_saving" : "graph.subject_save")}
          </Button>
        </>
      }
    >
      <form
        className="file-graph__subject-form"
        onSubmit={(event) => {
          event.preventDefault();
          save();
        }}
      >
        <Input
          autoFocus
          disabled={busy}
          label={t("graph.subject_name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <TextArea
          disabled={busy}
          label={t("graph.subject_describe")}
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
        {error && <p className="file-graph__form-error" role="alert">{error}</p>}
        {/* Lets Enter submit the form without a visible second button. */}
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Modal>
  );
};
