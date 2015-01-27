from flask import Blueprint, render_template, current_app, abort, g, \
    request, url_for, session, jsonify, flash
from galatea.tryton import tryton
from galatea.utils import get_tryton_language
from galatea.helpers import cached
from flask.ext.paginate import Pagination
from flask.ext.babel import format_date, gettext as _, lazy_gettext
from trytond.transaction import Transaction
from trytond.config import config as tryton_config
from whoosh import index
from whoosh.qparser import MultifieldParser
from datetime import datetime
import os

training = Blueprint('training', __name__, template_folder='templates')

DISPLAY_MSG = lazy_gettext('Displaying <b>{start} - {end}</b> of <b>{total}</b>')

GALATEA_WEBSITE = current_app.config.get('TRYTON_GALATEA_SITE')
SHOPS = current_app.config.get('TRYTON_SALE_SHOPS')
LIMIT = current_app.config.get('TRYTON_PAGINATION_CATALOG_LIMIT', 20)
WHOOSH_MAX_LIMIT = current_app.config.get('WHOOSH_MAX_LIMIT', 500)

Website = tryton.pool.get('galatea.website')
Template = tryton.pool.get('product.template')
Product = tryton.pool.get('product.product')
Date = tryton.pool.get('ir.date')

TRAINING_TEMPLATE_FIELD_NAMES = [
    'name', 'esale_slug', 'esale_shortdescription', 'esale_price',
    'esale_default_images', 'esale_all_images', 'esale_new', 'esale_hot',
    'esale_metakeyword', 'training_sessions',
    ]
TRAINING_PRODUCT_FIELD_NAMES = [
    'training_start_date', 'training_end_date', 'training_registration',
    'training_place.rec_name', 'training_seats', 'training_note', 'template',
    'add_cart', 'esale_quantity',
    ]
TRAINING_TEMPLATE_FILTERS = []
TRAINING_SCHEMA_PARSE_FIELDS = ['title', 'content']

@training.route("/json/trainings", endpoint="trainings-json")
@tryton.transaction()
@cached(3500, 'trainings-json')
def training_json(lang):
    '''JSON Current Training Sessions'''

    def date_handler(obj):
        return obj.isoformat() if hasattr(obj, 'isoformat') else obj

    # Current training sessions
    with Transaction().set_context(without_special_price=True):
        domain = [
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_active', '=', True),
            ('esale_saleshops', 'in', SHOPS),
            ('training', '=', True),
            ('training_start_date', '>=', Date.today()),
            ]
        order = [('training_start_date', 'ASC')]
        products = Product.search_read(domain, order=order,
            fields_names=TRAINING_PRODUCT_FIELD_NAMES)

    results = []
    for product in products:
        p = {}
        p['start_date'] = product['training_start_date'].strftime('%Y-%m-%d')
        p['end_date'] = product['training_start_date'].strftime('%Y-%m-%d')
        place = product['training_place.rec_name']
        p['place'] = place if place else ''
        template, = Template.read([product['template']],
            fields_names=TRAINING_TEMPLATE_FIELD_NAMES)
        p['name'] = template['name']
        p['url'] = '%s%s' % (current_app.config['BASE_URL'], url_for(
            'training.training', lang=g.language, slug=template['esale_slug']))
        p['shortdescription'] = template['esale_shortdescription']
        results.append(p)
    return jsonify(results=results)

@training.route("/json/<slug>", endpoint="training-detail-json")
@tryton.transaction()
@cached(3500, 'training-detail-json')
def training_detail_json(lang, slug):
    '''Training JSON Details

    slug param is a product slug or a product code
    '''
    with Transaction().set_context(without_special_price=True):
        products = Template.search([
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_slug', '=', slug),
            ('esale_active', '=', True),
            ('esale_saleshops', 'in', SHOPS),
            ('training', '=', True),
            ], limit=1)

    product = None
    if products:
        product, = products

    if not product:
        # search product by code
        with Transaction().set_context(without_special_price=True):
            products = Product.search([
                ('template.esale_available', '=', True),
                ('code', '=', slug),
                ('template.esale_active', '=', True),
                ('template.esale_saleshops', 'in', SHOPS),
                ], limit=1)
            if products:
                product = products[0].template

    if not product:
        abort(404)

    result = {}
    result['name'] = product.name
    result['url'] = '%s%s' % (current_app.config['BASE_URL'], url_for(
        'training.training', lang=g.language, slug=product.esale_slug))
    result['shortdescription'] = product.esale_shortdescription
    tsessions = []
    for s in product.training_sessions:
        tsession = {}
        tsession['start_date'] = s.training_start_date.strftime('%Y-%m-%d')
        tsession['end_date'] = s.training_start_date.strftime('%Y-%m-%d')
        place = s.training_place.rec_name
        tsession['place'] = place if place else ''
        tsessions.append(tsession)
    result['sessions'] = tsessions
    return jsonify(result)

@training.route("/search/", methods=["GET"], endpoint="search")
@tryton.transaction()
def search(lang):
    '''Search'''
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    WHOOSH_TRAINING_DIR = current_app.config.get('WHOOSH_TRAINING_DIR')
    if not WHOOSH_TRAINING_DIR:
        abort(404)

    db_name = current_app.config.get('TRYTON_DATABASE')
    locale = get_tryton_language(lang)

    schema_dir = os.path.join(tryton_config.get('database', 'path'),
        db_name, 'whoosh', WHOOSH_TRAINING_DIR, locale.lower())

    if not os.path.exists(schema_dir):
        abort(404)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.all', lang=g.language),
        'name': _('Training'),
        }, {
        'slug': url_for('.search', lang=g.language),
        'name': _('Search'),
        }]

    q = request.args.get('q')
    if not q:
        return render_template('training-search.html',
                webiste=website,
                products=[],
                breadcrumbs=breadcrumbs,
                pagination=None,
                q=None,
                )

    # Get products from schema results
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    # Search
    ix = index.open_dir(schema_dir)
    query = q.replace('+', ' AND ').replace('-', ' NOT ')
    query = MultifieldParser(TRAINING_SCHEMA_PARSE_FIELDS, ix.schema).parse(query)

    with ix.searcher() as s:
        all_results = s.search_page(query, 1, pagelen=WHOOSH_MAX_LIMIT)
        total = all_results.scored_length()
        results = s.search_page(query, page, pagelen=LIMIT) # by pagination
        res = [result.get('id') for result in results]

    domain = [('id', 'in', res)]
    order = [('name', 'ASC')]

    products = []
    with Transaction().set_context(without_special_price=True):
        order = [('name', 'ASC')]
        for t in Template.search_read(domain, order=order,
                fields_names=TRAINING_TEMPLATE_FIELD_NAMES):
            template = t.copy()
            sessions = t['training_sessions']
            if sessions:
                prods = Product.read(sessions, fields_names=TRAINING_PRODUCT_FIELD_NAMES)
                template['training_sessions'] = prods # add more info in training sessions field
            products.append(template)

    pagination = Pagination(page=page, total=total, per_page=LIMIT, display_msg=DISPLAY_MSG, bs_version='3')

    return render_template('training-search.html',
            website=website,
            products=products,
            pagination=pagination,
            breadcrumbs=breadcrumbs,
            q=q,
            )

@training.route("/<slug>", endpoint="training")
@tryton.transaction()
def training_detail(lang, slug):
    '''Training Details

    slug param is a product slug or a product code
    '''
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    # template
    template = request.args.get('template', None)
    if template:
        blueprintdir = os.path.dirname(__file__)
        basedir = '/'.join(blueprintdir.split('/')[:-1])
        if not os.path.isfile('%s/templates/%s.html' % (basedir, template)):
            template = None
    if not template:
        template = 'training'

    with Transaction().set_context(without_special_price=True):
        products = Template.search([
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_slug', '=', slug),
            ('esale_active', '=', True),
            ('esale_saleshops', 'in', SHOPS),
            ('training', '=', True),
            ], limit=1)

    product = None
    if products:
        product, = products

    if not product:
        # search product by code
        with Transaction().set_context(without_special_price=True):
            products = Product.search([
                ('template.esale_available', '=', True),
                ('code', '=', slug),
                ('template.esale_active', '=', True),
                ('template.esale_saleshops', 'in', SHOPS),
                ], limit=1)
            if products:
                product = products[0].template

    if not product:
        abort(404)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }, {
        'slug': url_for('.all', lang=g.language),
        'name': _('All'),
        }, {
        'slug': url_for('.training', lang=g.language, slug=product.esale_slug),
        'name': product.name,
        }]

    return render_template('%s.html' % template,
            website=website,
            breadcrumbs=breadcrumbs,
            product=product,
            )

@training.route("/key/<key>", endpoint="key")
@tryton.transaction()
def keys(lang, key):
    '''Training by Key'''
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    domain = [
        ('salable', '=', True),
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', SHOPS),
        ('training', '=', True),
        ('esale_metakeyword', 'ilike', '%'+key+'%'),
        ]
    total = Template.search_count(domain)

    offset = (page-1)*LIMIT

    products = []
    with Transaction().set_context(without_special_price=True):
        order = [('name', 'ASC')]
        templates = Template.search_read(domain, offset, LIMIT, order,
            TRAINING_TEMPLATE_FIELD_NAMES)
    if not templates:
        abort(404)

    for t in templates:
        template = t.copy()
        sessions = t['training_sessions']
        if sessions:
            prods = Product.read(sessions, fields_names=TRAINING_PRODUCT_FIELD_NAMES)
            template['training_sessions'] = prods # add more info in training sessions field
        products.append(template)

    pagination = Pagination(page=page, total=total, per_page=LIMIT, display_msg=DISPLAY_MSG, bs_version='3')

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }, {
        'slug': url_for('.key', lang=g.language, key=key),
        'name': key,
        }]

    return render_template('trainings-key.html',
            website=website,
            breadcrumbs=breadcrumbs,
            pagination=pagination,
            products=products,
            key=key,
            )

@training.route("/all/", methods=["GET", "POST"], endpoint="all")
@tryton.transaction()
def training_all(lang):
    '''All Training'''
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    domain_filter = session.get('training_filter', [])
    if request.form:
        domain_filter = []
        domain_filter_keys = set()
        for k, v in request.form.iteritems():
            if k in TRAINING_TEMPLATE_FILTERS:
                domain_filter_keys.add(k)

        for k in list(domain_filter_keys):
            domain_filter.append((k, 'in', request.form.getlist(k)))

    session['training_filter'] = domain_filter

    domain = [
        ('salable', '=', True),
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', SHOPS),
        ('training', '=', True),
        ] + domain_filter

    # Search
    if request.args.get('q'):
        qstr = request.args.get('q')
        q = '%' + qstr + '%'
        domain.append(
            ('rec_name', 'ilike', q),
            )
        session.q = qstr
        flash(_("Your search is \"%s\"." % qstr))
    else:
        session.q = None

    total = Template.search_count(domain)
    offset = (page-1)*LIMIT

    products = []
    with Transaction().set_context(without_special_price=True):
        order = [('name', 'ASC')]
        for t in Template.search_read(domain, offset, LIMIT, order, TRAINING_TEMPLATE_FIELD_NAMES):
            template = t.copy()
            sessions = t['training_sessions']
            if sessions:
                prods = Product.read(sessions, fields_names=TRAINING_PRODUCT_FIELD_NAMES)
                template['training_sessions'] = prods # add more info in training sessions field
            products.append(template)
    pagination = Pagination(page=page, total=total, per_page=LIMIT, display_msg=DISPLAY_MSG, bs_version='3')

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }, {
        'slug': url_for('.all', lang=g.language),
        'name': _('All'),
        }]

    return render_template('trainings-all.html',
            website=website,
            breadcrumbs=breadcrumbs,
            pagination=pagination,
            products=products,
            )

@training.route("/all/<date>", endpoint="trainings_by_date")
@tryton.transaction()
def training_list_by_date(lang, date):
    '''Training Sessions by date'''
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    try:
        date = datetime.strptime(date, "%Y-%m-%d")
    except:
        abort(404)

    # Current training sessions
    with Transaction().set_context(without_special_price=True):
        domain = [
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_active', '=', True),
            ('esale_saleshops', 'in', SHOPS),
            ('training', '=', True),
            ('training_start_date', '=', date),
            ]
        order = [('training_start_date', 'ASC')]
        products = Product.search_read(domain, order=order, fields_names=TRAINING_PRODUCT_FIELD_NAMES)

    templates = []
    if products:
        tpls = set()
        for product in products:
            tpls.add(product['template'])

        with Transaction().set_context(without_special_price=True):
            for t in Template.read(list(tpls), fields_names=TRAINING_TEMPLATE_FIELD_NAMES):
                template = t.copy()
                sessions = t['training_sessions']
                if sessions:
                    prods = Product.read(sessions, fields_names=TRAINING_PRODUCT_FIELD_NAMES)
                    template['training_sessions'] = prods # add more info in training sessions field
                templates.append(template)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }, {
        'slug': url_for('.all', lang=g.language),
        'name': _('Sessions')+' '+format_date(date, 'short'),
        }]

    return render_template('trainings-date.html',
            website=website,
            breadcrumbs=breadcrumbs,
            products=templates,
            date=date,
            )

@training.route("/", methods=["GET", "POST"], endpoint="trainings")
@tryton.transaction()
def training_list(lang):
    '''Current Training Sessions'''
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    domain_filter = session.get('training_filter', [])
    if request.form:
        domain_filter = []
        domain_filter_keys = set()
        for k, v in request.form.iteritems():
            if k in TRAINING_TEMPLATE_FILTERS:
                domain_filter_keys.add(k)

        for k in list(domain_filter_keys):
            domain_filter.append((k, 'in', request.form.getlist(k)))

    session['training_filter'] = domain_filter

    # Current training sessions
    with Transaction().set_context(without_special_price=True):
        domain = [
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_active', '=', True),
            ('esale_saleshops', 'in', SHOPS),
            ('training', '=', True),
            ('training_start_date', '>=', Date.today()),
            ] + domain_filter
        order = [('training_start_date', 'ASC')]
        products = Product.search_read(domain, order=order, fields_names=TRAINING_PRODUCT_FIELD_NAMES)

    templates = []
    if products:
        tpls = set()
        for product in products:
            tpls.add(product['template'])

        with Transaction().set_context(without_special_price=True):
            for t in Template.read(list(tpls), fields_names=TRAINING_TEMPLATE_FIELD_NAMES):
                template = t.copy()
                sessions = t['training_sessions']
                if sessions:
                    prods = Product.read(sessions, fields_names=TRAINING_PRODUCT_FIELD_NAMES)
                    template['training_sessions'] = prods # add more info in training sessions field
                templates.append(template)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }]

    return render_template('trainings.html',
            website=website,
            breadcrumbs=breadcrumbs,
            products=templates,
            )
