// Fixture: запрещённый импорт из соседнего frontend-модуля.
// Должен быть detect'ен скриптом AT-001.

import { Article } from "rehome-kb-platform/articles";
import { fetchKb } from "../kb-search/client";

export const x = { Article, fetchKb };
