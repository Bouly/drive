import { buildModel } from "../model";
import { GraphData, GraphFile } from "../types";

jest.mock("@gouvfr-lasuite/ui-components", () => ({
  getMimeCategory: () => "docs",
}));

const file = (topics: GraphFile["topics"]): GraphFile => ({
  id: "file",
  title: "Protection sociale.odt",
  mimetype: "application/vnd.oasis.opendocument.text",
  size: 100,
  created_at: "2026-09-17T10:00:00Z",
  updated_at: "2026-09-17T10:00:00Z",
  creator: "Demo",
  creator_id: "demo",
  role: "owner",
  topics,
});

const data = (topics: GraphFile["topics"]): GraphData => ({
  files: [file(topics)],
  links: [],
  subjects: [
    { id: "silent", name: "Subvention", description: "", strength: 0.02 },
    { id: "valid", name: "Droit", description: "", strength: 0.9 },
  ],
  scope: null,
  folders: [],
});

describe("subject membership on the graph", () => {
  it("uses the accepted subject for the node, label and filter", () => {
    const model = buildModel(data([
      { id: "silent", score: 1, pinned: false },
      { id: "valid", score: 0.8, pinned: false },
    ]));

    expect([...model.belongs[0]]).toEqual(["valid"]);
    expect(model.topics[model.clusters[0]].label).toBe("Droit");
    expect(model.topics[0].files).toEqual([]);
    expect(model.topics[0].members).toEqual([]);
    expect(model.topics[1].files).toEqual([0]);
    expect(model.topics[1].members).toEqual([0]);
  });

  it("leaves a file with only rejected subjects unclassified", () => {
    const model = buildModel(data([{ id: "silent", score: 1, pinned: false }]));

    expect(model.belongs[0].size).toBe(0);
    expect(model.clusters[0]).toBe(-1);
    expect(model.topics.every((topic) => topic.files.length === 0)).toBe(true);
  });

  it("keeps an explicitly pinned file even in a weak subject", () => {
    const model = buildModel(data([{ id: "silent", score: 1, pinned: true }]));

    expect([...model.belongs[0]]).toEqual(["silent"]);
    expect(model.topics[model.clusters[0]].members).toEqual([0]);
  });

  it("keeps the same classification when a folder hides empty subjects", () => {
    const scoped = data([
      { id: "silent", score: 1, pinned: false },
      { id: "valid", score: 0.8, pinned: false },
    ]);
    scoped.scope = { id: "folder", title: "Dossier", path: ["Dossier"] };
    const model = buildModel(scoped);

    expect(model.subjects.map((subject) => subject.id)).toEqual(["valid"]);
    expect(model.clusters[0]).toBe(0);
    expect(model.topics[0].files).toEqual(model.topics[0].members);
  });
});
