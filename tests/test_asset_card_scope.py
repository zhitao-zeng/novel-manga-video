from novel_manga.application.assets.cards import build_with_backoff


def test_character_redraw_does_not_expand_into_all_book_props(tmp_path):
    class Factory:
        def build_selected(self, root, bible, characters, locations, **options):
            assert characters == {'character_195'}
            assert locations == set()
            assert options['prop_ids'] == set(), 'None means every book prop to the factory'
            return 'one character only'
    assert build_with_backoff(Factory(), tmp_path, object(), {'character_195'}, set()) == 'one character only'
