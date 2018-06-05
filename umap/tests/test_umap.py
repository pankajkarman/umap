"""
Tests for UMAP to ensure things are working as expected.
"""
from nose.tools import assert_less
from nose.tools import assert_greater_equal
import os.path
import numpy as np
from scipy.spatial import distance
from scipy import sparse
from scipy import stats
from sklearn.utils.estimator_checks import check_estimator
from sklearn.utils.testing import (assert_equal,
                                   assert_array_equal,
                                   assert_array_almost_equal,
                                   assert_raises,
                                   assert_in,
                                   assert_not_in,
                                   assert_no_warnings,
                                   if_matplotlib)
from sklearn.metrics import pairwise_distances
from sklearn.neighbors import KDTree, BallTree
from sklearn.utils import shuffle
from sklearn.preprocessing import StandardScaler, normalize
from sklearn.manifold.t_sne import trustworthiness
from sklearn.cluster import KMeans
from scipy.stats import mode

from tempfile import mkdtemp
from functools import wraps
from nose import SkipTest

from sklearn import datasets

import umap.distances as dist
import umap.sparse as spdist
from umap.umap_ import (
    INT32_MAX,
    INT32_MIN,
    make_forest,
    rptree_leaf_array,
    nearest_neighbors,
    smooth_knn_dist,
    UMAP)

np.random.seed(42)
spatial_data = np.random.randn(10, 20)
spatial_data = np.vstack([spatial_data, np.zeros((2, 20))]) # Add some all zero data for corner case test
binary_data = np.random.choice(a=[False, True],
                               size=(10, 20),
                               p=[0.66, 1 - 0.66])
binary_data = np.vstack([binary_data, np.zeros((2, 20))]) # Add some all zero data for corner case test
sparse_spatial_data = sparse.csr_matrix(spatial_data * binary_data)
sparse_binary_data = sparse.csr_matrix(binary_data)

nn_data = np.random.uniform(0, 1, size=(1000, 5))
nn_data = np.vstack([nn_data, np.zeros((2, 5))]) # Add some all zero data for corner case test
binary_nn_data = np.random.choice(a=[False, True],
                                  size=(1000, 5),
                                  p=[0.66, 1 - 0.66])
binary_nn_data = np.vstack([binary_nn_data, np.zeros((2, 5))]) # Add some all zero data for corner case test
sparse_nn_data = sparse.csr_matrix(nn_data * binary_nn_data)

iris_selection = np.random.choice([True, False], 150,
                                  replace=True, p=[0.75, 0.25])

spatial_distances = (
    'euclidean',
    'manhattan',
    'chebyshev',
    'minkowski',
    'hamming',
    'canberra',
    'braycurtis',
    'cosine',
    'correlation'
)

binary_distances = (
    'jaccard',
    'matching',
    'dice',
    'kulsinski',
    'rogerstanimoto',
    'russellrao',
    'sokalmichener',
    'sokalsneath',
    'yule'
)


def test_nn_descent_neighbor_accuracy():
    knn_indices, knn_dists, _ = nearest_neighbors(nn_data, 10, 'euclidean', {}, False, np.random)

    tree = KDTree(nn_data)
    true_indices = tree.query(nn_data, 10, return_distance=False)

    num_correct = 0.0
    for i in range(nn_data.shape[0]):
        num_correct += np.sum(np.in1d(true_indices[i], knn_indices[i]))

    percent_correct = num_correct / (spatial_data.shape[0] * 10)
    assert_greater_equal(percent_correct, 0.99, 'NN-descent did not get 99% '
                         'accuracy on nearest neighbors')

def test_angular_nn_descent_neighbor_accuracy():
    knn_indices, knn_dists, _ = nearest_neighbors(nn_data, 10, 'cosine', {}, True, np.random)

    angular_data = normalize(nn_data, norm='l2')
    tree = KDTree(angular_data)
    true_indices = tree.query(angular_data, 10, return_distance=False)

    num_correct = 0.0
    for i in range(nn_data.shape[0]):
        num_correct += np.sum(np.in1d(true_indices[i], knn_indices[i]))

    percent_correct = num_correct / (spatial_data.shape[0] * 10)
    assert_greater_equal(percent_correct, 0.99, 'NN-descent did not get 99% '
                         'accuracy on nearest neighbors')


def test_sparse_nn_descent_neighbor_accuracy():
    knn_indices, knn_dists, _ = nearest_neighbors(sparse_nn_data, 10,
                                                  'euclidean', {}, False,
                                                  np.random)

    tree = KDTree(sparse_nn_data.todense())
    true_indices = tree.query(sparse_nn_data.todense(),
                              10, return_distance=False)

    num_correct = 0.0
    for i in range(nn_data.shape[0]):
        num_correct += np.sum(np.in1d(true_indices[i], knn_indices[i]))

    percent_correct = num_correct / (spatial_data.shape[0] * 10)
    assert_greater_equal(percent_correct, 0.99, 'Sparse NN-descent did not get '
                                                '99% accuracy on nearest '
                                                'neighbors')


def test_sparse_angular_nn_descent_neighbor_accuracy():
    knn_indices, knn_dists, _ = nearest_neighbors(sparse_nn_data, 10, 'cosine', {}, True, np.random)

    angular_data = normalize(sparse_nn_data, norm='l2').toarray()
    tree = KDTree(angular_data)
    true_indices = tree.query(angular_data, 10, return_distance=False)

    num_correct = 0.0
    for i in range(nn_data.shape[0]):
        num_correct += np.sum(np.in1d(true_indices[i], knn_indices[i]))

    percent_correct = num_correct / (spatial_data.shape[0] * 10)
    assert_greater_equal(percent_correct, 0.99, 'NN-descent did not get 99% '
                         'accuracy on nearest neighbors')

def test_smooth_knn_dist_l1norms():
    knn_indices, knn_dists, _ = nearest_neighbors(nn_data, 10,
                                                  'euclidean', {}, False,
                                                  np.random)
    sigmas, rhos = smooth_knn_dist(knn_dists, 10)
    shifted_dists = knn_dists - rhos[:, np.newaxis]
    shifted_dists[shifted_dists < 0.0] = 0.0
    vals = np.exp(-(shifted_dists/sigmas[:, np.newaxis]))
    norms = np.sum(vals, axis=1)

    assert_array_almost_equal(norms,
                              1.0 + np.log2(10) * np.ones(norms.shape[0]),
                              decimal=3,
                              err_msg='Smooth knn-dists does not give expected'
                                      'norms')



def test_metrics():
    for metric in spatial_distances:
        dist_matrix = pairwise_distances(spatial_data, metric=metric)
        # scipy is bad sometimes
        if metric == 'braycurtis':
            dist_matrix[np.where(~np.isfinite(dist_matrix))] = 0.0
        if metric in ('cosine', 'correlation'):
            dist_matrix[np.where(~np.isfinite(dist_matrix))] = 1.0
            # And because distance between all zero vectors should be zero
            dist_matrix[10, 11] = 0.0
            dist_matrix[11, 10] = 0.0
        dist_function = dist.named_distances[metric]
        test_matrix = np.array([[dist_function(spatial_data[i], spatial_data[j])
                                 for j in range(spatial_data.shape[0])]
                                for i in range(spatial_data.shape[0])])
        assert_array_almost_equal(test_matrix, dist_matrix,
                                  err_msg="Distances don't match "
                                          "for metric {}".format(metric))

    for metric in binary_distances:
        dist_matrix = pairwise_distances(binary_data, metric=metric)
        if metric in ('jaccard', 'dice', 'sokalsneath', 'yule'):
            dist_matrix[np.where(~np.isfinite(dist_matrix))] = 0.0
        if metric in ('kulsinski', 'russellrao'):
            dist_matrix[np.where(~np.isfinite(dist_matrix))] = 0.0
            # And because distance between all zero vectors should be zero
            dist_matrix[10, 11] = 0.0
            dist_matrix[11, 10] = 0.0
        dist_function = dist.named_distances[metric]
        test_matrix = np.array([[dist_function(binary_data[i], binary_data[j])
                                 for j in range(binary_data.shape[0])]
                                for i in range(binary_data.shape[0])])
        assert_array_almost_equal(test_matrix, dist_matrix,
                                  err_msg="Distances don't match "
                                          "for metric {}".format(metric))

    # Handle the few special distances separately
    # SEuclidean
    v = np.abs(np.random.randn(spatial_data.shape[1]))
    dist_matrix = pairwise_distances(spatial_data, metric='seuclidean', V=v)
    test_matrix = np.array([[dist.standardised_euclidean(spatial_data[i], spatial_data[j], v)
                             for j in range(spatial_data.shape[0])]
                            for i in range(spatial_data.shape[0])])
    assert_array_almost_equal(test_matrix, dist_matrix,
                              err_msg="Distances don't match "
                                      "for metric seuclidean")

    # Weighted minkowski
    dist_matrix = pairwise_distances(spatial_data, metric='wminkowski', w=v, p=3)
    test_matrix = np.array([[dist.weighted_minkowski(spatial_data[i], spatial_data[j], v, p=3)
                             for j in range(spatial_data.shape[0])]
                            for i in range(spatial_data.shape[0])])
    assert_array_almost_equal(test_matrix, dist_matrix,
                              err_msg="Distances don't match "
                                      "for metric weighted_minkowski")
    # Mahalanobis
    v = np.abs(np.random.randn(spatial_data.shape[1], spatial_data.shape[1]))
    dist_matrix = pairwise_distances(spatial_data, metric='mahalanobis', VI=v)
    test_matrix = np.array([[dist.mahalanobis(spatial_data[i], spatial_data[j], v)
                             for j in range(spatial_data.shape[0])]
                            for i in range(spatial_data.shape[0])])
    assert_array_almost_equal(test_matrix, dist_matrix,
                              err_msg="Distances don't match "
                                      "for metric mahalanobis")
    # Haversine
    tree = BallTree(spatial_data[: ,:2], metric='haversine')
    dist_matrix, _ = tree.query(spatial_data[: ,:2], k=spatial_data.shape[0])
    test_matrix = np.array([[dist.haversine(spatial_data[i, :2], spatial_data[j, :2])
                             for j in range(spatial_data.shape[0])]
                            for i in range(spatial_data.shape[0])])
    test_matrix.sort(axis=1)
    assert_array_almost_equal(test_matrix, dist_matrix,
                              err_msg="Distances don't match "
                                      "for metric haversine")


def test_sparse_metrics():
    for metric in spatial_distances:
        # Sparse correlation has precision errors right now, leave out ...
        if metric in spdist.sparse_named_distances and metric is not \
                'correlation':
            dist_matrix = pairwise_distances(sparse_spatial_data.todense(),
                                             metric=metric)
            if metric in ('braycurtis', 'dice', 'sokalsneath', 'yule'):
                dist_matrix[np.where(~np.isfinite(dist_matrix))] = 0.0
            if metric in ('cosine', 'correlation', 'kulsinski', 'russellrao'):
                dist_matrix[np.where(~np.isfinite(dist_matrix))] = 1.0
                # And because distance between all zero vectors should be zero
                dist_matrix[10, 11] = 0.0
                dist_matrix[11, 10] = 0.0

            dist_function = spdist.sparse_named_distances[metric]
            if metric in spdist.sparse_need_n_features:
                test_matrix = np.array(
                    [[dist_function(sparse_spatial_data[i].indices,
                                    sparse_spatial_data[i].data,
                                    sparse_spatial_data[j].indices,
                                    sparse_spatial_data[j].data,
                                    sparse_spatial_data.shape[1])
                        for j in range(sparse_spatial_data.shape[0])]
                     for i in range(sparse_spatial_data.shape[0])])
            else:
                test_matrix = np.array(
                    [[dist_function(sparse_spatial_data[i].indices,
                                    sparse_spatial_data[i].data,
                                    sparse_spatial_data[j].indices,
                                    sparse_spatial_data[j].data)
                        for j in range(sparse_spatial_data.shape[0])]
                     for i in range(sparse_spatial_data.shape[0])])

            assert_array_almost_equal(test_matrix, dist_matrix,
                                      err_msg="Sparse distances don't match "
                                              "for metric {}".format(metric))

    for metric in binary_distances:
        # Sparse correlation has precision errors right now, leave out ...
        if metric in spdist.sparse_named_distances:
            dist_matrix = pairwise_distances(sparse_binary_data.todense(),
                                             metric=metric)
            if metric in ('jaccard', 'dice', 'sokalsneath', 'yule'):
                dist_matrix[np.where(~np.isfinite(dist_matrix))] = 0.0
            if metric in ('kulsinski', 'russellrao'):
                dist_matrix[np.where(~np.isfinite(dist_matrix))] = 1.0
                # And because distance between all zero vectors should be zero
                dist_matrix[10, 11] = 0.0
                dist_matrix[11, 10] = 0.0

            dist_function = spdist.sparse_named_distances[metric]
            if metric in spdist.sparse_need_n_features:
                test_matrix = np.array(
                    [[dist_function(sparse_binary_data[i].indices,
                                    sparse_binary_data[i].data,
                                    sparse_binary_data[j].indices,
                                    sparse_binary_data[j].data,
                                    sparse_binary_data.shape[1])
                        for j in range(sparse_binary_data.shape[0])]
                     for i in range(sparse_binary_data.shape[0])])
            else:
                test_matrix = np.array(
                    [[dist_function(sparse_binary_data[i].indices,
                                    sparse_binary_data[i].data,
                                    sparse_binary_data[j].indices,
                                    sparse_binary_data[j].data)
                        for j in range(sparse_binary_data.shape[0])]
                     for i in range(sparse_binary_data.shape[0])])

            assert_array_almost_equal(test_matrix, dist_matrix,
                                      err_msg="Sparse distances don't match "
                                              "for metric {}".format(metric))

def test_umap_sparse_trustworthiness():
    embedding = UMAP(n_neighbors=10).fit_transform(sparse_nn_data[:100])
    trust = trustworthiness(sparse_nn_data[:100].toarray(), embedding, 10)
    assert_greater_equal(trust, 0.92, 'Insufficiently trustworthy embedding for'
                                      'sparse test dataset: {}'.format(trust))

def test_umap_trustworthiness_on_iris():
    iris = datasets.load_iris()
    data = iris.data
    embedding = UMAP(n_neighbors=10, min_dist=0.01,
                     random_state=42).fit_transform(data)
    trust = trustworthiness(iris.data, embedding, 10)
    assert_greater_equal(trust, 0.97, 'Insufficiently trustworthy embedding for'
                                      'iris dataset: {}'.format(trust))

def test_umap_transform_on_iris():
    iris = datasets.load_iris()
    data = iris.data[iris_selection]
    fitter = UMAP(n_neighbors=10, min_dist=0.01,
                     random_state=42).fit(data)

    new_data = iris.data[~iris_selection]
    embedding = fitter.transform(new_data)

    trust = trustworthiness(new_data, embedding, 10)
    assert_greater_equal(trust, 0.95, 'Insufficiently trustworthy transform for'
                                      'iris dataset: {}'.format(trust))

def test_multi_component_layout():
    data, labels = datasets.make_blobs(100, 2, centers=5, cluster_std=0.5,
                                  center_box=[-20, 20], random_state=42)

    true_centroids = np.empty((labels.max() + 1, data.shape[1]), dtype=np.float64)

    for label in range(labels.max() + 1):
        true_centroids[label] = data[labels == label].mean(axis=0)

    true_centroids = normalize(true_centroids, norm='l2')

    embedding = UMAP(n_neighbors=4).fit_transform(data)
    embed_centroids = np.empty((labels.max() + 1, data.shape[1]), dtype=np.float64)
    embed_labels = KMeans(n_clusters=5).fit_predict(embedding)

    for label in range(embed_labels.max() + 1):
        embed_centroids[label] = data[embed_labels == label].mean(axis=0)

    embed_centroids = normalize(embed_centroids, norm='l2')

    error = np.sum((true_centroids - embed_centroids)**2)

    assert_less(error, 15.0, msg='Multi component embedding to far astray')
