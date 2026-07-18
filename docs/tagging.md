# Book tags — vocabulary & fill-in playbook

`books.tags` holds hand-curated subject tags so anyone browsing the catalog can
pull up a whole group of books ("show me the vol trading books"). Online
metadata lookup never sets it — new registrations start untagged, and this file
is the standing instruction for filling them in later.

**Reusable prompt:** "fill in the missing tags following docs/tagging.md".

## Format rules

- Comma-and-space separated, lowercase English: `systematic theology, luther`.
- 1–3 tags per book. The **first tag is the primary category** (the "shelf");
  later tags are cross-references.
- Reuse the vocabulary below before inventing a tag. Add a new tag only when at
  least ~3 books would carry it — and add it to this file in the same change.
- When filling *missing* tags, never overwrite rows that already have tags
  (they may have been corrected by hand) unless explicitly asked.
- The SQLite DB is the source of truth. Never edit `catalog.json` — it is
  regenerated on every publish.

## Vocabulary

Tags are listed with their scope and example authors already in this library.

### Theology & Bible

| Tag | Scope / examples |
|---|---|
| `systematic theology` | Doctrine-centred works: Calvin, Bavinck, Chafer, Horton, Kuyper, Moltmann, covenant theology |
| `biblical theology` | Whole-Bible themes as a discipline: Vos, Beale, Klink |
| `hermeneutics` | How to interpret Scripture: Thiselton, Zimmermann, Carson's *Exegetical Fallacies*, Fee & Stuart, 正意解经 |
| `biblical commentary` | Commentary on one biblical book: Hamilton/Walton on Genesis, Hartley on Job, 馮象《先知書》, Ryle on Matthew |
| `old testament studies` | OT history, criticism, narrative art: Van Seters, Fokkelman, Provan/Long/Longman, 摩西五經導論 |
| `new testament studies` | NT and Pauline studies: Westerholm, Hays' backdrop works, Vanhoozer's NT interpretation |
| `bible & study aids` | Bibles themselves, study Bibles, biblical-language grammars: ESV editions, Wenham's NT Greek, Stibbs |
| `luther` | Luther and Luther studies: 路德文集, the Galatians/Romans lectures, Table Talk, Bayer, Becker, Forde |
| `barth` | Barth and Barth studies: 教會教義學, Gibson & Strange, Morrison, 曾劭愷 |
| `church history` | Movements, eras, figures: Machen, Noll, Carl Henry, Brainerd's biography, Olson |
| `christian living` | Devotion, sanctification, counselling: Powlison, Bridges, Ferguson, Lloyd-Jones, Ryle's *Holiness* |
| `ministry & preaching` | Preaching and church ministry: Buechner, Willimon, Keller, Lovelace |
| `christian ethics` | Ethics from Scripture: Hays (NT), Wright (OT), Daniel Bell |
| `public theology` | Faith in public life: Volf, Hauerwas & Willimon, Niebuhr, Gordon Graham |

### History, archaeology & society

| Tag | Scope / examples |
|---|---|
| `ancient near east history` | ANE history/archaeology/texts: Pritchard, Kenyon, Woolley, Saggs, Gordon, Matthews |
| `ancient chinese history` | Pre-imperial China, bronzes, oracle bones: 张光直, 许宏, 李学勤, 李硕《翦商》, 许倬云, 顾颉刚 |
| `modern chinese history` | 19th c.–present China: 沈志華, 龍應台, Joel Andreas |
| `twentieth century history` | Totalitarianism, war, Cold War: Klemperer, Milton Mayer, 弯曲的脊梁, GDR reading culture |
| `mythology & religion studies` | Comparative myth and religion: Eliade, Hooke, 中国创世神话, the Babylonian flood legend |
| `reportage` | Long-form on places and people: Peter Hessler (何偉) titles, *The Buried* |
| `politics & society` | Secular political/social thought: Sowell, the Federalist Papers |

### Science & technology

| Tag | Scope / examples |
|---|---|
| `physics` | Textbooks and monographs: Feynman lectures, Landau & Lifshitz, Cohen-Tannoudji, QFT/condensed-matter texts (Mattuck, Abrikosov, 文小刚, Nagaosa, Anderson) |
| `mathematics` | Math proper: Chong & Zak optimization, 贝叶斯的博弈, 小平邦彦 |
| `machine learning` | ML/AI/data science: Bishop's PRML, *Build a Large Language Model*, Schutt & O'Neil |
| `programming` | Software craft: the Rust book, Fowler's *Refactoring*, 人月神话, Hacker's Delight, low-latency C++ |
| `popular science` | Science for general readers, science memoir, reference: Munroe's xkcd books, GEB, Pinker, 朗道传, Apollo Remastered, astronomy handbooks |

### Finance & markets

| Tag | Scope / examples |
|---|---|
| `vol trading` | Volatility as an asset: Euan Sinclair, Colin Bennett, Derman & Miller's *Volatility Smile* |
| `options & derivatives` | Options/futures theory and practice: Hull, 品味期权, 期权投资盈利之道 |
| `quant finance` | Stochastic calculus, time series, stat arb, quant culture: Shreve, Tsay, Andrew Pole, Derman's memoir, Taleb, econophysics, HFT systems |
| `equity investment` | Stock picking and fundamentals — reserved; no holdings yet, kept for future additions |

### Literature & arts

| Tag | Scope / examples |
|---|---|
| `fiction` | Novels, stories, plays not covered below: Austen, Scott, Ted Chiang, Tolkien, Frayn's *Copenhagen*, 马伯庸 |
| `spy fiction` | The espionage shelf: John le Carré, Graham Greene's *The Human Factor* |
| `chinese literature` | Chinese poetry, essays, classics: 北島, 董橋, 古文观止, 世說新語, 六神磊磊 |
| `comics & graphic novels` | Watchmen, Maus, 乒乓, the DC volumes, Tom Gauld |
| `essays & humor` | Thurber, Chesterton's essays |
| `philosophy` | Charles Taylor, Kant studies, existentialism, Tillich/Pannenberg (cross-tag with theology) |
| `biography & memoir` | Lives and memoirs: 蕃薯人的故事, Gorz's *Letter to D*, Agatha Christie's dig memoir, sports narratives |
| `books about books` | Book collecting and reading culture: 旧书与珍本, 西书东藏, *Revenge of the Librarians* |

### Other

| Tag | Scope / examples |
|---|---|
| `stamp catalog` | Philately reference: さくら日本切手カタログ (owner's call: its own tag, not folded elsewhere) |

Cross-tag freely when a book truly straddles groups: Luther's Galatians
lectures → `luther, biblical commentary`; *The Gospel According to Peanuts* →
`christian living, comics & graphic novels`; 金融物理学 → `quant finance, physics`.
A one-off oddity gets the nearest existing tag rather than a new one, unless the
owner rules otherwise (see below).

### Settled rulings (owner-decided — don't re-litigate)

- 统计信仰 (徐鸿鹄) → `mathematics` (statistics content, despite the title)
- 重建范型 (Denis Alexander) → `public theology`
- さくら日本切手カタログ → `stamp catalog`
- *Jane legt het uit* → `physics`

## Fill-in procedure

1. **List the untagged books** (data dir may differ — check `local_config/config.yml`):

   ```sh
   sqlite3 data/lib_admin.sqlite \
     "SELECT isbn, title, author FROM books WHERE tags IS NULL OR tags = '' ORDER BY title;"
   ```

2. **Choose tags** for each book from the vocabulary, using title + author. If a
   title is ambiguous (e.g. bare `Biblical criticism`), look the ISBN up online
   instead of guessing.

3. **Apply in one transaction**, one UPDATE per book, keyed by ISBN:

   ```sh
   sqlite3 data/lib_admin.sqlite <<'SQL'
   BEGIN;
   UPDATE books SET tags = 'vol trading, options & derivatives' WHERE isbn = '9781119943970';
   -- ... one line per book ...
   COMMIT;
   SQL
   ```

   (Single books can also be edited in the app: admin → collection → Edit →
   Tags, which PATCHes `/api/book/<isbn>`.)

4. **Verify**: re-run the query from step 1 (should be empty, or only the books
   deliberately skipped), then eyeball the group sizes:

   ```sh
   sqlite3 data/lib_admin.sqlite \
     "SELECT trim(substr(tags, 1, instr(tags || ',', ',') - 1)) AS primary_tag,
             count(*) FROM books WHERE tags IS NOT NULL AND tags != ''
      GROUP BY primary_tag ORDER BY count(*) DESC;"
   ```

5. **Publish**: tags reach the public viewer on the next catalog publish —
   `python scripts/publish_catalog.py`, or wait for `run_local.py`'s 12-hour
   auto-publish. New tags added to the vocabulary should be committed to this
   file in the same change.
