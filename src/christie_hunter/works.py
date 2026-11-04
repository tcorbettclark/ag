"""Canonical list of works authored by Agatha Christie, with title variants
(US retitles etc.) used to build per-title search queries.

Sources: her published canon (novels, short story collections, Westmacott
romances written under her pseudonym, non-fiction). Continuation novels by
other authors ("The Monogram Murders", the Lucy Foley reimaginings, etc.) are
deliberately absent: they are not written by Christie.
"""

# (canonical title, [search variants including canonical title])
NOVELS = [
    ("The Mysterious Affair at Styles", ["The Mysterious Affair at Styles"]),
    ("The Secret Adversary", ["The Secret Adversary"]),
    ("The Man in the Brown Suit", ["The Man in the Brown Suit"]),
    ("The Secret of Chimneys", ["The Secret of Chimneys"]),
    ("The Murder of Roger Ackroyd", ["The Murder of Roger Ackroyd"]),
    ("The Big Four", ["The Big Four"]),
    ("The Mystery of the Blue Train", ["The Mystery of the Blue Train"]),
    ("The Seven Dials Mystery", ["The Seven Dials Mystery"]),
    ("The Murder at the Vicarage", ["The Murder at the Vicarage"]),
    ("The Sittaford Mystery", ["The Sittaford Mystery", "The Murder at Hazelmoor"]),
    ("Peril at End House", ["Peril at End House"]),
    ("Lord Edgware Dies", ["Lord Edgware Dies", "Thirteen at Dinner"]),
    ("Murder on the Orient Express", ["Murder on the Orient Express", "Murder in the Calais Coach"]),
    ("Why Didn't They Ask Evans?", ["Why Didn't They Ask Evans", "The Boomerang Clue"]),
    ("Three Act Tragedy", ["Three Act Tragedy", "Murder in Three Acts"]),
    ("The A.B.C. Murders", ["The ABC Murders", "The Alphabet Murders"]),
    ("Murder in Mesopotamia", ["Murder in Mesopotamia"]),
    ("Cards on the Table", ["Cards on the Table"]),
    ("Dumb Witness", ["Dumb Witness", "Poirot Loses a Client"]),
    ("Death on the Nile", ["Death on the Nile"]),
    ("Appointment with Death", ["Appointment with Death"]),
    ("Hercule Poirot's Christmas", ["Hercule Poirot's Christmas", "Murder for Christmas", "Holiday for Murder"]),
    ("Murder Is Easy", ["Murder Is Easy", "Easy to Kill"]),
    ("And Then There Were None", ["And Then There Were None", "Ten Little Indians"]),
    ("Sad Cypress", ["Sad Cypress"]),
    ("Evil Under the Sun", ["Evil Under the Sun"]),
    ("N or M?", ["N or M"]),
    ("The Body in the Library", ["The Body in the Library"]),
    ("Five Little Pigs", ["Five Little Pigs", "Murder in Retrospect"]),
    ("The Moving Finger", ["The Moving Finger", "The Case of the Moving Finger"]),
    ("Towards Zero", ["Towards Zero"]),
    ("Death Comes as the End", ["Death Comes as the End"]),
    ("Sparkling Cyanide", ["Sparkling Cyanide", "Remembered Death"]),
    ("The Hollow", ["The Hollow", "Murder After Hours"]),
    ("Taken at the Flood", ["Taken at the Flood", "There Is a Tide"]),
    ("Crooked House", ["Crooked House"]),
    ("A Murder Is Announced", ["A Murder Is Announced"]),
    ("They Came to Baghdad", ["They Came to Baghdad"]),
    ("Mrs McGinty's Dead", ["Mrs McGinty's Dead", "Blood Will Tell"]),
    ("They Do It with Mirrors", ["They Do It with Mirrors", "Murder with Mirrors"]),
    ("After the Funeral", ["After the Funeral", "Funerals Are Fatal"]),
    ("A Pocket Full of Rye", ["A Pocket Full of Rye"]),
    ("Destination Unknown", ["Destination Unknown", "So Many Steps to Death"]),
    ("Hickory Dickory Dock", ["Hickory Dickory Dock"]),
    ("Dead Man's Folly", ["Dead Man's Folly"]),
    ("4.50 from Paddington", ["4.50 from Paddington", "4:50 from Paddington", "What Mrs McGillicuddy Saw"]),
    ("Ordeal by Innocence", ["Ordeal by Innocence"]),
    ("Cat Among the Pigeons", ["Cat Among the Pigeons"]),
    ("The Pale Horse", ["The Pale Horse"]),
    ("The Mirror Crack'd from Side to Side", ["The Mirror Crack'd from Side to Side", "The Mirror Crack'd"]),
    ("The Clocks", ["The Clocks"]),
    ("A Caribbean Mystery", ["A Caribbean Mystery"]),
    ("At Bertram's Hotel", ["At Bertram's Hotel"]),
    ("Third Girl", ["Third Girl"]),
    ("Endless Night", ["Endless Night"]),
    ("By the Pricking of My Thumbs", ["By the Pricking of My Thumbs"]),
    ("Passenger to Frankfurt", ["Passenger to Frankfurt", "The Passenger to Frankfurt"]),
    ("Nemesis", ["Nemesis"]),
    ("Elephants Can Remember", ["Elephants Can Remember"]),
    ("Postern of Fate", ["Postern of Fate"]),
    ("Curtain", ["Curtain: Poirot's Last Case", "Curtain Poirot's Last Case"]),
    ("Sleeping Murder", ["Sleeping Murder"]),
]

WESTMACOTT = [
    ("Giant's Bread", ["Giant's Bread"]),
    ("Unfinished Portrait", ["Unfinished Portrait"]),
    ("Absent in the Spring", ["Absent in the Spring"]),
    ("The Rose and the Yew Tree", ["The Rose and the Yew Tree"]),
    ("A Daughter's Daughter", ["A Daughter's Daughter"]),
    ("The Burden", ["The Burden"]),
]

COLLECTIONS = [
    ("Poirot Investigates", ["Poirot Investigates"]),
    ("Partners in Crime", ["Partners in Crime"]),
    ("The Mysterious Mr Quin", ["The Mysterious Mr Quin", "The Mysterious Mr Quinn"]),
    ("The Thirteen Problems", ["The Thirteen Problems", "The Tuesday Club Murders"]),
    ("The Hound of Death", ["The Hound of Death"]),
    ("The Listerdale Mystery", ["The Listerdale Mystery"]),
    ("Parker Pyne Investigates", ["Parker Pyne Investigates", "Mr Parker Pyne Detective"]),
    ("Murder in the Mews", ["Murder in the Mews"]),
    ("The Regatta Mystery", ["The Regatta Mystery"]),
    ("The Labours of Hercules", ["The Labours of Hercules", "The Labors of Hercules"]),
    ("The Witness for the Prosecution", ["The Witness for the Prosecution"]),
    ("Three Blind Mice", ["Three Blind Mice"]),
    ("The Under Dog", ["The Under Dog and Other Stories"]),
    ("The Adventure of the Christmas Pudding", ["The Adventure of the Christmas Pudding"]),
    ("Double Sin", ["Double Sin and Other Stories"]),
    ("The Golden Ball", ["The Golden Ball and Other Stories"]),
    ("Poirot's Early Cases", ["Poirot's Early Cases", "Hercule Poirot's Early Cases"]),
    ("Miss Marple's Final Cases", ["Miss Marple's Final Cases"]),
    ("Problem at Pollensa Bay", ["Problem at Pollensa Bay"]),
    ("The Harlequin Tea Set", ["The Harlequin Tea Set"]),
    ("While the Light Lasts", ["While the Light Lasts"]),
]

NON_FICTION = [
    ("Come, Tell Me How You Live", ["Come Tell Me How You Live"]),
    ("An Autobiography", ["Agatha Christie An Autobiography"]),
    ("Star Over Bethlehem", ["Star Over Bethlehem"]),
    ("The Road of Dreams", ["The Road of Dreams"]),
]


def all_search_queries():
    """Yield (work_key, search_query) pairs for per-title searches."""
    for title, variants in NOVELS + WESTMACOTT + COLLECTIONS + NON_FICTION:
        for v in variants:
            yield (title, f"agatha christie {v}")


WORKS = NOVELS + WESTMACOTT + COLLECTIONS + NON_FICTION