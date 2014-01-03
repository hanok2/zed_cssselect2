from __future__ import unicode_literals

import re

from tinycss2.nth import parse_nth

from . import parser
from .parser import SelectorError


# http://dev.w3.org/csswg/selectors/#whitespace
split_whitespace = re.compile('[^ \t\r\n\f]+').findall


def compile_selector_list(input, namespaces=None):
    """Compile a (comma-separated) list of selectors.

    :param input:
        A :term:`tinycss2:string`,
        or an iterable of tinycss2 :term:`tinycss2:component values`
        such as the :attr:`~tinycss2.ast.QualifiedRule.predule` of a style rule.
    :param namespaces:
        A optional dictionary of all `namespace prefix declarations
        <http://www.w3.org/TR/selectors/#nsdecl>`_ in scope for this selector.
        Keys are namespace prefixes as strings, or ``None`` for the default
        namespace.
        Values are namespace URLs as strings.
        If omitted, assume that no prefix is declared.
    :returns:
        A list of opaque :class:`CompiledSelector` objects.

    """
    return [
        CompiledSelector(selector)
        for selector in parser.parse(input, namespaces)
    ]


class CompiledSelector(object):
    def __init__(self, parsed_selector):
        source = _compile_node(parsed_selector.parsed_tree)
        self.never_matches = source == '0'
        self.test = eval(
            'lambda el: ' + source,
            {'split_whitespace': split_whitespace},
            {},
        )
        self.specificity = parsed_selector.specificity
        self.pseudo_element = parsed_selector.pseudo_element
        self.id = None
        self.class_name = None
        self.local_name = None
        self.namespace = None

        node = parsed_selector.parsed_tree
        if isinstance(node, parser.CombinedSelector):
            node = node.right
        for simple_selector in node.simple_selectors:
            if isinstance(simple_selector, parser.IDSelector):
                self.id = simple_selector.ident
            elif isinstance(simple_selector, parser.ClassSelector):
                self.class_name = simple_selector.class_name
            elif isinstance(simple_selector, parser.LocalNameSelector):
                self.local_name = simple_selector.local_name
            elif isinstance(simple_selector, parser.NamespaceSelector):
                self.namespace = simple_selector.namespace


def _compile_node(selector):
    """Return a boolean expression, as a Python source string.

    When evaluated in a context where the `el` variable is an
    :class:`~cssselect2.tree.Element` object,
    tells whether the element is a subject of `selector`.

    """
    # To avoid precedence-related bugs, any sub-expression that is passed
    # around must be "atomic": add parentheses when the top-level would be
    # an operator. Bare literals and function calls are fine.

    # 1 and 0 are used for True and False to avoid global lookups.

    if isinstance(selector, parser.CombinedSelector):
        left_inside = _compile_node(selector.left)
        if left_inside == '0':
            return '0'  # 0 and x == 0
        elif left_inside == '1':
            # 1 and x == x, but the element matching 1 still needs to exist.
            if selector.combinator in (' ', '>'):
                left = 'el.parent is not None'
            elif selector.combinator in ('~', '+'):
                left = 'el.previous is not None'
            else:
                raise SelectorError('Unknown combinator', selector.combinator)
        # Rebind the `el` name inside a generator-expressions (in a new scope)
        # so that 'left_inside' applies to different elements.
        elif selector.combinator == ' ':
            left = 'any((%s) for el in el.iter_ancestors())' % left_inside
        elif selector.combinator == '>':
            left = ('next(el is not None and (%s) for el in [el.parent])'
                    % left_inside)
        elif selector.combinator == '+':
            left = ('next(el is not None and (%s) for el in [el.previous])'
                    % left_inside)
        elif selector.combinator == '~':
            left = ('any((%s) for el in el.iter_previous_siblings())'
                    % left_inside)
        else:
            raise SelectorError('Unknown combinator', selector.combinator)

        right = _compile_node(selector.right)
        if right == '0':
            return '0'  # 0 and x == 0
        elif right == '1':
            return left  # 1 and x == x
        else:
            # Evaluate combinators right to left:
            return '(%s) and (%s)' % (right, left)

    elif isinstance(selector, parser.CompoundSelector):
        sub_expressions = [
            expr for expr in map(_compile_node, selector.simple_selectors)
            if expr != '1']
        if len(sub_expressions) == 1:
            test = sub_expressions[0]
        elif '0' in sub_expressions:
            test = '0'
        elif sub_expressions:
            test = ' and '.join('(%s)' % e for e in sub_expressions)
        else:
            test = '1'  # all([]) == True

        if isinstance(selector, parser.NegationSelector):
            if test == '0':
                return '1'
            elif test == '1':
                return '0'
            else:
                return 'not (%s)' % test
        else:
            return test

    elif isinstance(selector, parser.LocalNameSelector):
        return 'el.local_name == %r' % selector.local_name

    elif isinstance(selector, parser.NamespaceSelector):
        return 'el.namespace_url == %r' % selector.namespace

    elif isinstance(selector, parser.ClassSelector):
        return '%r in el.classes' % selector.class_name

    elif isinstance(selector, parser.IDSelector):
        return 'el.id == %r' % selector.ident

    elif isinstance(selector, parser.AttributeSelector):
        if selector.namespace is not None:
            if selector.namespace:
                key = '{%s}%s' % (selector.namespace, selector.name)
            else:
                key = selector.name
            value = selector.value
            if selector.operator is None:
                return 'el.get_attr(%r) is not None' % key
            elif selector.operator == '=':
                return 'el.get_attr(%r) == %r' % (key, value)
            elif selector.operator == '~=':
                if len(value.split()) != 1 or value.strip() != value:
                    return '0'
                else:
                    return ('%r in split_whitespace(el.get_attr(%r, ""))'
                            % (value, key))
            elif selector.operator == '|=':
                return ('next(v == %r or (v is not None and v.startswith(%r))'
                        '     for v in [el.get_attr(%r)])'
                        % (value, value + '-', key))
            elif selector.operator == '^=':
                if value:
                    return 'el.get_attr(%r, "").startswith(%r)' % (key, value)
                else:
                    return '0'
            elif selector.operator == '$=':
                if value:
                    return 'el.get_attr(%r, "").endswith(%r)' % (key, value)
                else:
                    return '0'
            elif selector.operator == '*=':
                if value:
                    return '%r in el.get_attr(%r, "")' % (value, key)
                else:
                    return '0'
            else:
                raise SelectorError(
                    'Unknown attribute operator', selector.operator)
        else:  # In any namespace
            raise NotImplementedError  # TODO

    elif isinstance(selector, parser.PseudoClassSelector):
        if selector.name == 'link':
            return ('%s and el.get_attr("href") is not None'
                     % html_tag_eq('a', 'area', 'link'))
        # TODO: for :disabled and :enabled on HTML elements
        # 'button', 'input', 'select', 'textarea', and 'option',
        # "… or if it is a descendant of a fieldset element
        #    whose disabled attribute is set and is not a descendant
        #    of that fieldset element's first legend element child, if any."
        # http://www.whatwg.org/C#concept-fe-disabled
        elif selector.name == 'enabled':
            return (
                '(%s and el.get_attr("disabled") is None) or '
                '(%s and el.get_attr("href") is not None)'
                % (
                    html_tag_eq('button', 'input', 'select', 'textarea',
                                'option', 'optgroup', 'menuitem', 'fieldset'),
                    html_tag_eq('a', 'area', 'link'),
                )
            )
        elif selector.name == 'disabled':
            return (
                '%s and el.get_attr("disabled") is not None'
                % html_tag_eq('button', 'input', 'select', 'textarea',
                              'option', 'optgroup', 'menuitem', 'fieldset')
            )
        elif selector.name == 'checked':
            return (
                '(%s and el.get_attr("checked") is not None) or '
                '(%s and el.get_attr("selected") is not None)'
                % (
                    html_tag_eq('input', 'menuitem'),
                    html_tag_eq('option'),
                )
            )
        elif selector.name in ('visited', 'hover', 'active', 'focus',
                               'target'):
            # Not applicable in a static context: never match.
            return '0'
        elif selector.name == 'root':
            return 'el.parent is None'
        elif selector.name == 'first-child':
            return 'el.index == 0'
        elif selector.name == 'last-child':
            return 'el.index + 1 == len(el.etree_siblings)'
        elif selector.name == 'first-of-type':
            return ('all(s.tag != el.etree_element.tag'
                    '    for s in el.etree_siblings[:el.index])')
        elif selector.name == 'last-of-type':
            return ('all(s.tag != el.etree_element.tag'
                    '    for s in el.etree_siblings[el.index + 1:])')
        elif selector.name == 'only-child':
            return 'len(el.etree_siblings) == 1'
        elif selector.name == 'only-of-type':
            return ('all(s.tag != el.etree_element.tag or i == el.index'
                    '    for i, s in enumerate(el.etree_siblings))')
        elif selector.name == 'empty':
            return 'not (el.etree_children or el.etree_element.text)'
        else:
            raise SelectorError('Unknown pseudo-class', selector.name)

    elif isinstance(selector, parser.FunctionalPseudoClassSelector):
        if selector.name == 'lang':
            tokens = [
                t for t in selector.arguments
                if t.type != 'whitespace'
            ]
            if len(tokens) == 1 and tokens[0].type == 'ident':
                lang = tokens[0].value
            else:
                raise SelectorError('Invalid arguments for :lang()')

            # TODO: matching should be case-insensitive
            return ('el.lang == %r or '
                    '(el.lang is not None and el.lang.startswith(%r))'
                    % (lang, lang + '-'))
        else:
            if selector.name == 'nth-child':
                count = 'el.index'
            elif selector.name == 'nth-last-child':
                count = '(len(el.etree_siblings) - el.index - 1)'
            elif selector.name == 'nth-of-type':
                count = ('sum(1 for s in el.etree_siblings[:el.index]'
                         '    if s.tag == el.etree_element.tag)')
            elif selector.name == 'nth-last-of-type':
                count = ('sum(1 for s in el.etree_siblings[el.index + 1:]'
                         '    if s.tag == el.etree_element.tag)')
            else:
                raise SelectorError('Unknown pseudo-class', selector.name)

            result = parse_nth(selector.arguments)
            if result is None:
                raise SelectorError(
                    'Invalid arguments for :%s()' % selector.name)
            a, b = result
            # x is the number of siblings before/after the element
            # Matches if a positive or zero integer n exists so that:
            # x = a*n + b-1
            # x = a*n + B
            B = b - 1
            if a == 0:
                # x = B
                return '%s == %i' % (count, B)
            else:
                # n = (x - B) / a
                return ('next(r == 0 and n >= 0'
                        '     for n, r in [divmod(%s - %i, %i)])'
                        % (count, B, a))

    else:
        raise TypeError(type(selector), selector)


def html_tag_eq(*local_names):
    if len(local_names) == 1:
        return '(el.etree_element.tag == %r)' % (
            '{http://www.w3.org/1999/xhtml}' + local_names[0])
    else:
        return '(el.etree_element.tag in (%s))' % ', '.join(
            repr('{http://www.w3.org/1999/xhtml}' + n) for n in local_names)
