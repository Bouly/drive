import { useTranslation } from "react-i18next";
import { Button, Modal, ModalSize, useResponsive } from "@gouvfr-lasuite/ui-components";

type DuplicateModalProps = {
  /** The file the card is open on, and the one holding the same content. */
  kept: string;
  dropped: string;
  /** True while the file is on its way to the trash. */
  busy?: boolean;
  onClose: () => void;
  onConfirm: () => void;
};

/**
 * Confirming that one of two identical files may go.
 *
 * Both names are written out, because the graph is the only place where the
 * two ever appear side by side: they can sit in different folders, under
 * different names, and the reader is about to drop one of them on the word
 * of a percentage. The one that stays is named too, so the choice is between
 * two files rather than "delete this".
 */
export const DuplicateModal = ({
  kept,
  dropped,
  busy,
  onClose,
  onConfirm,
}: DuplicateModalProps) => {
  const { t } = useTranslation();
  const { isDesktop } = useResponsive();

  return (
    <Modal
      isOpen
      closeOnClickOutside
      onClose={onClose}
      size={isDesktop ? ModalSize.SMALL : ModalSize.FULL}
      title={t("graph.duplicate_title")}
      aria-label={t("graph.duplicate_title")}
      rightActions={
        <>
          <Button variant="tertiary" onClick={onClose}>
            {t("graph.duplicate_cancel")}
          </Button>
          <Button color="error" disabled={busy} onClick={onConfirm}>
            {t("graph.duplicate_confirm")}
          </Button>
        </>
      }
    >
      <div className="file-graph__duplicate-body">
        <p>{t("graph.duplicate_explain")}</p>
        <p className="file-graph__duplicate-file file-graph__duplicate-file--dropped">{dropped}</p>
        <p className="file-graph__duplicate-keeps">{t("graph.duplicate_keeps")}</p>
        <p className="file-graph__duplicate-file">{kept}</p>
        <p className="file-graph__duplicate-trash">{t("graph.duplicate_trash")}</p>
      </div>
    </Modal>
  );
};
