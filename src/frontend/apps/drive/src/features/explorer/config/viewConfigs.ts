import { DefaultRoute } from "@/utils/defaultRoutes";
import { ViewConfig } from "../types/viewConfig";

export const VIEW_CONFIGS: Record<DefaultRoute | "folder", ViewConfig> = {
  [DefaultRoute.RECENT]: {
    defaultOrdering: "-updated_at",
    folderMode: "files_only",
    sortable: false,
  },
  [DefaultRoute.MY_FILES]: {
    defaultOrdering: "-type,title",
    folderMode: "folders_first",
  },
  [DefaultRoute.SHARED_WITH_ME]: {
    defaultOrdering: "-updated_at",
    folderMode: "mixed",
  },
  [DefaultRoute.FAVORITES]: {
    defaultOrdering: "-type,title",
    folderMode: "folders_first",
  },
  [DefaultRoute.TRASH]: {
    defaultOrdering: "-type,-updated_at",
    folderMode: "folders_first",
  },
  // The graph page has no item list; this entry only satisfies the record type.
  [DefaultRoute.GRAPH]: {
    defaultOrdering: "-updated_at",
    folderMode: "mixed",
    sortable: false,
  },
  folder: {
    defaultOrdering: "-type,title",
    folderMode: "folders_first",
  },
};
