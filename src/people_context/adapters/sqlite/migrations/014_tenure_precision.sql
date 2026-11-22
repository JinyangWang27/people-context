-- A CV says "2015-2019", never a day. Each bound of an affiliation or an education row is the edge
-- of the span its source stated, and its precision says how much of that date was stated, so a
-- year is kept as a year instead of being invented into a day or dropped. Existing rows were all
-- stored as exact days, which is what the default records.
ALTER TABLE affiliations ADD COLUMN valid_from_precision TEXT NOT NULL DEFAULT 'day'
    CHECK (valid_from_precision IN ('day', 'month', 'year'));
ALTER TABLE affiliations ADD COLUMN valid_to_precision TEXT NOT NULL DEFAULT 'day'
    CHECK (valid_to_precision IN ('day', 'month', 'year'));
ALTER TABLE education ADD COLUMN valid_from_precision TEXT NOT NULL DEFAULT 'day'
    CHECK (valid_from_precision IN ('day', 'month', 'year'));
ALTER TABLE education ADD COLUMN valid_to_precision TEXT NOT NULL DEFAULT 'day'
    CHECK (valid_to_precision IN ('day', 'month', 'year'));
